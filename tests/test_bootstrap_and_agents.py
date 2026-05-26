from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.domains.agents.schemas import AgentDTO
from app.domains.bootstrap.schemas import (
    BootstrapResponse,
    MeContextResponse,
    PlanDTO,
    ProfileDTO,
    SubscriptionDTO,
)


class _DummyVerifier:
    def verify_token(self, token: str) -> dict[str, str]:
        if token == "good-token":
            return {"sub": "00000000-0000-0000-0000-000000000123"}
        raise AppError(code="auth.unauthorized", message="bad token", status_code=401)


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer good-token"}


def _make_profile() -> ProfileDTO:
    return ProfileDTO.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000123",
            "full_name": None,
            "avatar_url": None,
            "timezone": "UTC",
            "email_notifications_enabled": True,
            "notification_preferences": {},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )


def _make_plan(max_agents: int = 3) -> PlanDTO:
    return PlanDTO.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000777",
            "slug": "hobby",
            "name": "Hobby",
            "monthly_price_cents": 2900,
            "included_conversations": 200,
            "max_agents": max_agents,
            "overage_conversation_cents": 8,
            "features": {},
        }
    )


def _make_subscription() -> SubscriptionDTO:
    return SubscriptionDTO.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000555",
            "user_id": "00000000-0000-0000-0000-000000000123",
            "plan_id": "00000000-0000-0000-0000-000000000777",
            "status": "active",
            "current_period_start": "2026-01-01T00:00:00Z",
            "current_period_end": "2026-01-31T23:59:59Z",
            "cancel_at_period_end": False,
            "provider_customer_id": None,
            "provider_subscription_id": None,
        }
    )


def _make_agent(name: str = "Support", slug: str = "support") -> AgentDTO:
    return AgentDTO.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000888",
            "user_id": "00000000-0000-0000-0000-000000000123",
            "name": name,
            "slug": slug,
            "public_key": "pub_123",
            "system_prompt": "",
            "model": "gpt-4o-mini",
            "behavior_settings": {},
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "archived_at": None,
        }
    )


def _patch_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())


def test_bootstrap_idempotency(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    state: dict[str, int] = {"count": 0}

    async def _bootstrap(*_: Any, **__: Any) -> BootstrapResponse:
        state["count"] += 1
        return BootstrapResponse(
            profile=_make_profile(),
            subscription=_make_subscription(),
            plan=_make_plan(),
            onboarding_completed=True,
        )

    monkeypatch.setattr("app.api.routes.bootstrap.bootstrap_me", _bootstrap)
    first = client.post("/api/v1/bootstrap/me", headers=_auth_header())
    second = client.post("/api/v1/bootstrap/me", headers=_auth_header())
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert state["count"] == 2


def test_me_onboarding_gate(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _gate(*_: Any, **__: Any) -> bool:
        return False

    monkeypatch.setattr("app.api.routes.bootstrap.user_dashboard_onboarding_completed", _gate)
    response = client.get("/api/v1/me/onboarding-gate", headers=_auth_header())
    assert response.status_code == 200
    assert response.json() == {"onboarding_completed": False}


def test_me_context_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _context(*_: Any, **__: Any) -> MeContextResponse:
        return MeContextResponse(
            profile=_make_profile(),
            subscription=_make_subscription(),
            plan=_make_plan(),
            usage_snapshot=None,
            onboarding_completed=True,
        )

    monkeypatch.setattr("app.api.routes.bootstrap.fetch_me_context", _context)
    response = client.get("/api/v1/me/context", headers=_auth_header())
    assert response.status_code == 200
    assert response.json()["plan"]["max_agents"] == 3


def test_create_agent_success_under_limit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _create(*_: Any, **__: Any) -> AgentDTO:
        return _make_agent()

    monkeypatch.setattr("app.api.routes.agents.create_agent", _create)
    response = client.post("/api/v1/agents", headers=_auth_header(), json={"name": "Support"})
    assert response.status_code == 200
    assert response.json()["name"] == "Support"


def test_create_agent_fails_over_limit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _create(*_: Any, **__: Any) -> AgentDTO:
        raise AppError(
            code="plan.limit_exceeded",
            message="Agent limit reached for current plan",
            status_code=409,
            details={"max_agents": 1, "current_agents": 1},
        )

    monkeypatch.setattr("app.api.routes.agents.create_agent", _create)
    response = client.post("/api/v1/agents", headers=_auth_header(), json={"name": "Support"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plan.limit_exceeded"


def test_list_agents_only_owned(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _list(*_: Any, **__: Any) -> list[AgentDTO]:
        return [_make_agent(name="One", slug="one"), _make_agent(name="Two", slug="two")]

    monkeypatch.setattr("app.api.routes.agents.list_agents", _list)
    response = client.get("/api/v1/agents", headers=_auth_header())
    assert response.status_code == 200
    assert len(response.json()["agents"]) == 2


def test_patch_agent_rejects_unknown_fields(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _update(*_: Any, **__: Any) -> AgentDTO:
        return _make_agent(name="Renamed")

    monkeypatch.setattr("app.api.routes.agents.update_agent", _update)
    response = client.patch(
        "/api/v1/agents/00000000-0000-0000-0000-000000000888",
        headers=_auth_header(),
        json={"unknown": "field"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request.validation_error"


def test_patch_agent_not_found(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _update(*_: Any, **__: Any) -> AgentDTO:
        raise AppError(code="agent.not_found", message="Agent not found", status_code=404)

    monkeypatch.setattr("app.api.routes.agents.update_agent", _update)
    response = client.patch(
        "/api/v1/agents/00000000-0000-0000-0000-000000000999",
        headers=_auth_header(),
        json={"name": "Renamed"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "agent.not_found"

