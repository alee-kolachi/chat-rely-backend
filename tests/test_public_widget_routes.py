import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import app.api.routes.public_widget as public_widget_routes
from app.core.errors import AppError
from app.domains.public_widget.schemas import PublicWidgetAgentContext, PublicWidgetConfigResponse
from app.domains.public_widget.service import (
    attachments_ui_enabled_for_plan_slug,
    hide_powered_by_chatrely_for_plan_slug,
)


def test_public_widget_config_missing_header(client: TestClient) -> None:
    r = client.get("/api/v1/public/widget/config")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "widget.missing_key"


def test_public_widget_config_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    aid = uuid4()
    uid = uuid4()
    ctx = PublicWidgetAgentContext(
        agent_id=aid,
        user_id=uid,
        name="Store Bot",
        behavior_settings={"brand_color": "#3B82F6", "widget_position": "bottom_left"},
    )

    async def _resolve(_db: Any, _key: str) -> PublicWidgetAgentContext:
        return ctx

    async def _plan_hobby(_db: Any, _uid: UUID) -> str:
        return "hobby"

    monkeypatch.setattr(public_widget_routes, "resolve_agent_for_widget_key", _resolve)
    monkeypatch.setattr("app.domains.public_widget.service.fetch_active_plan_slug", _plan_hobby)
    r = client.get("/api/v1/public/widget/config", headers={"X-ChatRely-Agent-Key": "test-key"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Store Bot"
    assert body["brand_color"] == "#3B82F6"
    assert body["widget_position"] == "bottom_left"
    assert body["agent_id"] == str(aid)
    assert isinstance(body.get("attachments_ui_enabled"), bool)
    assert body.get("hide_powered_by_chatrely") is False
    assert body.get("message_feedback_enabled") is False


def test_public_widget_config_hides_powered_by_on_pro(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    aid = uuid4()
    uid = uuid4()
    ctx = PublicWidgetAgentContext(
        agent_id=aid,
        user_id=uid,
        name="Pro Bot",
        behavior_settings={},
    )

    async def _resolve(_db: Any, _key: str) -> PublicWidgetAgentContext:
        return ctx

    async def _plan_pro(_db: Any, _uid: UUID) -> str:
        return "pro"

    monkeypatch.setattr(public_widget_routes, "resolve_agent_for_widget_key", _resolve)
    monkeypatch.setattr("app.domains.public_widget.service.fetch_active_plan_slug", _plan_pro)
    r = client.get("/api/v1/public/widget/config", headers={"X-ChatRely-Agent-Key": "test-key"})
    assert r.status_code == 200
    assert r.json().get("hide_powered_by_chatrely") is True
    assert r.json().get("message_feedback_enabled") is True


def test_attachments_ui_enabled_for_plan_slug() -> None:
    """Upload UI stays off until visitor attachments ship."""
    assert attachments_ui_enabled_for_plan_slug("free") is False
    assert attachments_ui_enabled_for_plan_slug("hobby") is False
    assert attachments_ui_enabled_for_plan_slug("standard") is False
    assert attachments_ui_enabled_for_plan_slug("pro") is False
    assert attachments_ui_enabled_for_plan_slug(None) is False
    assert attachments_ui_enabled_for_plan_slug("") is False


def test_hide_powered_by_chatrely_for_plan_slug() -> None:
    assert hide_powered_by_chatrely_for_plan_slug("pro") is True
    assert hide_powered_by_chatrely_for_plan_slug("PRO") is True
    assert hide_powered_by_chatrely_for_plan_slug("scale") is True
    assert hide_powered_by_chatrely_for_plan_slug("free") is False
    assert hide_powered_by_chatrely_for_plan_slug("hobby") is False
    assert hide_powered_by_chatrely_for_plan_slug("standard") is False
    assert hide_powered_by_chatrely_for_plan_slug(None) is False


def test_public_widget_cors_preflight(client: TestClient) -> None:
    r = client.options(
        "/api/v1/public/widget/config",
        headers={
            "Origin": "https://example.myshopify.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-chatrely-agent-key",
        },
    )
    assert r.status_code == 204
    assert r.headers.get("access-control-allow-origin") == "https://example.myshopify.com"


def test_public_chat_stream_cors_preflight(client: TestClient) -> None:
    r = client.options(
        "/api/chat/public/stream",
        headers={
            "Origin": "https://shop.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-chatrely-agent-key",
        },
    )
    assert r.status_code == 204
    assert r.headers.get("access-control-allow-origin") == "https://shop.example"


def test_public_widget_feedback_remove_accepts_body(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    aid = uuid4()
    uid = uuid4()
    ctx = PublicWidgetAgentContext(
        agent_id=aid,
        user_id=uid,
        name="Bot",
        behavior_settings={},
    )

    async def _resolve(_db: Any, _key: str) -> PublicWidgetAgentContext:
        return ctx

    async def _plan_pro(_db: Any, _uid: UUID) -> str:
        return "pro"

    recorded: list[dict[str, Any]] = []

    async def _upsert(**kwargs: Any) -> None:
        recorded.append(kwargs)

    monkeypatch.setattr(public_widget_routes, "resolve_agent_for_widget_key", _resolve)
    monkeypatch.setattr("app.domains.public_widget.service.fetch_active_plan_slug", _plan_pro)
    monkeypatch.setattr(public_widget_routes, "public_upsert_feedback", _upsert)

    mid = uuid4()
    r = client.post(
        "/api/v1/public/widget/message-feedback",
        headers={"X-ChatRely-Agent-Key": "test-key"},
        json={
            "message_id": str(mid),
            "visitor_id": "visitor-a",
            "remove": True,
        },
    )
    assert r.status_code == 204
    assert recorded and recorded[0].get("remove") is True


@pytest.mark.asyncio
async def test_resolve_conversation_rejects_wrong_visitor() -> None:
    from app.core.errors import AppError
    from app.domains.runtime.service import _resolve_or_create_conversation

    conv_id = uuid4()
    user_id = uuid4()
    agent_id = uuid4()

    class _Row:
        def mappings(self):
            return self

        def first(self):
            return {"id": str(conv_id), "visitor_id": "visitor-a"}

    class _Db:
        async def execute(self, *_args: Any, **_kwargs: Any) -> _Row:
            return _Row()

    with pytest.raises(AppError) as exc:
        await _resolve_or_create_conversation(
            _Db(),  # type: ignore[arg-type]
            user_id,
            agent_id,
            "visitor-b",
            conv_id,
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "conversation.forbidden"


def test_public_widget_message_feedback_forbidden_on_hobby(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    aid = uuid4()
    uid = uuid4()
    ctx = PublicWidgetAgentContext(
        agent_id=aid,
        user_id=uid,
        name="Bot",
        behavior_settings={},
    )

    async def _resolve(_db: Any, _key: str) -> PublicWidgetAgentContext:
        return ctx

    async def _plan_hobby(_db: Any, _uid: UUID) -> str:
        return "hobby"

    monkeypatch.setattr(public_widget_routes, "resolve_agent_for_widget_key", _resolve)
    monkeypatch.setattr("app.domains.public_widget.service.fetch_active_plan_slug", _plan_hobby)
    r = client.post(
        "/api/v1/public/widget/message-feedback",
        headers={"X-ChatRely-Agent-Key": "k"},
        json={"message_id": str(uuid4()), "visitor_id": "visitor-1", "value": -1},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "plan.message_feedback_not_available"


def test_public_widget_config_response_model() -> None:
    cfg = PublicWidgetConfigResponse(
        agent_id=uuid4(),
        name="N",
        brand_color=None,
        widget_position="bottom_right",
    )
    assert cfg.widget_position == "bottom_right"
    assert cfg.attachments_ui_enabled is False
    assert cfg.hide_powered_by_chatrely is False
    assert cfg.message_feedback_enabled is False
