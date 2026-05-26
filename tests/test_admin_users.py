"""Routing-layer tests for /api/v1/admin/users.

Mirrors the mocking pattern used by other tests under backend/tests: the service
functions are monkeypatched so we don't need DB seed data. The DB query layer is
exercised by integration via the rest of the suite.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AuthError
from app.core.settings import get_settings
from app.domains.admin.schemas import (
    AdminAgentSummary,
    AdminConversationListItem,
    AdminKnowledgeSummary,
    AdminSubscriptionSummary,
    AdminUsageSnapshotSummary,
    AdminUserDetail,
    AdminUserListItem,
    AdminUserListResponse,
)
from app.main import create_app


ADMIN_EMAIL = "alice@chatrely.com"
ADMIN_USER_ID = "00000000-0000-0000-0000-000000000aaa"
NON_ADMIN_EMAIL = "bob@example.com"
NON_ADMIN_USER_ID = "00000000-0000-0000-0000-000000000bbb"
SAMPLE_USER_ID = "00000000-0000-0000-0000-0000000abcde"


class _DummyVerifier:
    def verify_token(self, token: str) -> dict[str, str]:
        if token == "admin-token":
            return {"sub": ADMIN_USER_ID, "email": ADMIN_EMAIL}
        if token == "non-admin-token":
            return {"sub": NON_ADMIN_USER_ID, "email": NON_ADMIN_EMAIL}
        raise AuthError("bad token")


async def _noop_warmup(_self: object) -> None:
    return None


def _make_list_item(email: str = "x@y.com") -> AdminUserListItem:
    return AdminUserListItem(
        id=UUID(SAMPLE_USER_ID),
        email=email,
        full_name="Sample User",
        signed_up_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        plan_slug="free",
        plan_name="Free",
        subscription_status="active",
        agents_count=2,
        conversations_mtd=11,
        last_activity_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DEV_AUTH_BYPASS_ENABLED", "false")
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN_EMAIL)
    get_settings.cache_clear()
    monkeypatch.setattr("app.core.security.TokenVerifier.warmup", _noop_warmup)
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())
    app = create_app()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def _admin_auth() -> dict[str, str]:
    return {"Authorization": "Bearer admin-token"}


def _non_admin_auth() -> dict[str, str]:
    return {"Authorization": "Bearer non-admin-token"}


def test_list_users_admin_returns_paginated_shape(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_list(_db: Any, **kwargs: Any) -> AdminUserListResponse:
        captured.update(kwargs)
        return AdminUserListResponse(
            items=[_make_list_item("alice@x.com"), _make_list_item("bob@x.com")],
            total=42,
            page=int(kwargs.get("page", 1)),
            page_size=int(kwargs.get("page_size", 50)),
        )

    monkeypatch.setattr("app.api.routes.admin.users.list_admin_users", fake_list)

    response = admin_client.get(
        "/api/v1/admin/users",
        params={"q": "ali", "sort_by": "email", "sort_dir": "asc", "page": 2, "page_size": 25},
        headers=_admin_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 42
    assert body["page"] == 2
    assert body["page_size"] == 25
    assert len(body["items"]) == 2
    assert body["items"][0]["email"] == "alice@x.com"

    assert captured["search"] == "ali"
    assert captured["sort_by"] == "email"
    assert captured["sort_dir"] == "asc"
    assert captured["page"] == 2
    assert captured["page_size"] == 25


def test_list_users_defaults_have_no_search_and_signed_up_desc(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_list(_db: Any, **kwargs: Any) -> AdminUserListResponse:
        captured.update(kwargs)
        return AdminUserListResponse(items=[], total=0, page=1, page_size=50)

    monkeypatch.setattr("app.api.routes.admin.users.list_admin_users", fake_list)

    response = admin_client.get("/api/v1/admin/users", headers=_admin_auth())
    assert response.status_code == 200
    assert captured["search"] is None
    assert captured["sort_by"] == "signed_up_at"
    assert captured["sort_dir"] == "desc"
    assert captured["page"] == 1
    assert captured["page_size"] == 50


def test_list_users_rejects_invalid_sort_by(admin_client: TestClient) -> None:
    response = admin_client.get(
        "/api/v1/admin/users",
        params={"sort_by": "drop table users"},
        headers=_admin_auth(),
    )
    assert response.status_code == 422


def test_list_users_caps_page_size(admin_client: TestClient) -> None:
    response = admin_client.get(
        "/api/v1/admin/users",
        params={"page_size": 9999},
        headers=_admin_auth(),
    )
    assert response.status_code == 422


def test_list_users_non_admin_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get("/api/v1/admin/users", headers=_non_admin_auth())
    assert response.status_code == 404


def test_list_users_no_token_returns_401(admin_client: TestClient) -> None:
    response = admin_client.get("/api/v1/admin/users")
    assert response.status_code == 401


def test_get_user_detail_returns_full_shape(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_id = uuid4()
    sub_id = uuid4()
    agent_id = uuid4()
    conv_id = uuid4()
    snap_id = uuid4()

    async def fake_detail(_db: Any, user_id: UUID) -> AdminUserDetail:
        assert user_id == target_id
        return AdminUserDetail(
            id=target_id,
            email="alice@x.com",
            full_name="Alice",
            avatar_url=None,
            timezone="UTC",
            signed_up_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            subscriptions=[
                AdminSubscriptionSummary(
                    id=sub_id,
                    plan_slug="standard",
                    plan_name="Growth",
                    monthly_price_cents=2900,
                    status="active",
                    provider="stripe",
                    provider_customer_id="cus_X",
                    provider_subscription_id="sub_X",
                    current_period_start=datetime(2026, 5, 1, tzinfo=timezone.utc),
                    current_period_end=datetime(2026, 6, 1, tzinfo=timezone.utc),
                    cancel_at_period_end=False,
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
            ],
            agents=[
                AdminAgentSummary(
                    id=agent_id,
                    name="Support Bot",
                    slug="support",
                    model="gpt-4o-mini",
                    status="active",
                    conversations_total=120,
                    conversations_mtd=15,
                    created_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
                    archived_at=None,
                )
            ],
            recent_conversations=[
                AdminConversationListItem(
                    id=conv_id,
                    started_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
                    last_activity_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
                    status="open",
                    channel="widget",
                    visitor_id="visitor-1",
                    agent_id=agent_id,
                    agent_name="Support Bot",
                    user_id=target_id,
                    user_email="alice@x.com",
                    customer_message_count=2,
                    assistant_message_count=2,
                    tool_call_count=0,
                    total_input_tokens=100,
                    total_output_tokens=80,
                    fallback_used=False,
                    latest_message_preview="Hi there",
                )
            ],
            knowledge_summary=AdminKnowledgeSummary(
                by_kind={"website": 3, "file": 2},
                total_sources=5,
                total_chunks=240,
            ),
            recent_usage_snapshots=[
                AdminUsageSnapshotSummary(
                    id=snap_id,
                    period_start=datetime(2026, 5, 1).date(),
                    period_end=datetime(2026, 6, 1).date(),
                    included_conversations=100,
                    conversations_used=60,
                    overage_conversations=0,
                    estimated_overage_cents=0,
                    projected_conversations=85,
                    throttle_tier="normal",
                    last_computed_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
                )
            ],
        )

    monkeypatch.setattr("app.api.routes.admin.users.get_admin_user_detail", fake_detail)

    response = admin_client.get(
        f"/api/v1/admin/users/{target_id}",
        headers=_admin_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "alice@x.com"
    assert body["subscriptions"][0]["plan_slug"] == "standard"
    assert body["agents"][0]["name"] == "Support Bot"
    assert body["recent_conversations"][0]["agent_name"] == "Support Bot"
    assert body["knowledge_summary"]["by_kind"]["website"] == 3
    assert body["knowledge_summary"]["total_chunks"] == 240
    assert body["recent_usage_snapshots"][0]["throttle_tier"] == "normal"


def test_get_user_detail_non_admin_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get(
        f"/api/v1/admin/users/{uuid4()}",
        headers=_non_admin_auth(),
    )
    assert response.status_code == 404
