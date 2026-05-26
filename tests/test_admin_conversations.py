"""Routing-layer tests for /api/v1/admin/conversations."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AuthError
from app.core.settings import get_settings
from app.domains.admin.schemas import (
    AdminConversationDetail,
    AdminConversationListItem,
    AdminConversationListResponse,
    AdminMessageDTO,
)
from app.main import create_app


ADMIN_EMAIL = "alice@chatrely.com"
ADMIN_USER_ID = "00000000-0000-0000-0000-000000000aaa"
NON_ADMIN_EMAIL = "bob@example.com"
NON_ADMIN_USER_ID = "00000000-0000-0000-0000-000000000bbb"


class _DummyVerifier:
    def verify_token(self, token: str) -> dict[str, str]:
        if token == "admin-token":
            return {"sub": ADMIN_USER_ID, "email": ADMIN_EMAIL}
        if token == "non-admin-token":
            return {"sub": NON_ADMIN_USER_ID, "email": NON_ADMIN_EMAIL}
        raise AuthError("bad token")


async def _noop_warmup(_self: object) -> None:
    return None


def _make_conv_item(
    *,
    user_email: str = "alice@x.com",
    status: str = "open",
    fallback_used: bool = False,
) -> AdminConversationListItem:
    return AdminConversationListItem(
        id=uuid4(),
        started_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
        last_activity_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
        status=status,
        channel="widget",
        visitor_id="visitor-1",
        agent_id=uuid4(),
        agent_name="Support Bot",
        user_id=uuid4(),
        user_email=user_email,
        customer_message_count=2,
        assistant_message_count=2,
        tool_call_count=0,
        total_input_tokens=100,
        total_output_tokens=80,
        fallback_used=fallback_used,
        latest_message_preview="hello",
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


def test_list_conversations_passes_all_filters_to_service(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_list(_db: Any, **kwargs: Any) -> AdminConversationListResponse:
        captured.update(kwargs)
        return AdminConversationListResponse(items=[_make_conv_item()], total=1, page=1, page_size=50)

    monkeypatch.setattr(
        "app.api.routes.admin.conversations.list_admin_conversations", fake_list
    )

    agent_id = uuid4()
    user_id = uuid4()
    response = admin_client.get(
        "/api/v1/admin/conversations",
        params={
            "user_id": str(user_id),
            "user_email": "alice@",
            "agent_id": str(agent_id),
            "status": "open",
            "channel": "widget",
            "visitor_id": "visitor-1",
            "started_after": "2026-05-01T00:00:00+00:00",
            "started_before": "2026-06-01T00:00:00+00:00",
            "escalated": "false",
            "fallback_used": "true",
            "page": 3,
            "page_size": 25,
        },
        headers=_admin_auth(),
    )
    assert response.status_code == 200
    assert captured["user_id"] == user_id
    assert captured["user_email"] == "alice@"
    assert captured["agent_id"] == agent_id
    assert captured["status"] == "open"
    assert captured["channel"] == "widget"
    assert captured["visitor_id"] == "visitor-1"
    assert captured["started_after"] == datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert captured["started_before"] == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert captured["escalated"] is False
    assert captured["fallback_used"] is True
    assert captured["page"] == 3
    assert captured["page_size"] == 25


def test_list_conversations_filters_by_user_email_only_returns_matches(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_list(_db: Any, **kwargs: Any) -> AdminConversationListResponse:
        # Stub-side filter so the assertion below is meaningful even with monkeypatching.
        items = [_make_conv_item(user_email="alice@x.com"), _make_conv_item(user_email="bob@x.com")]
        needle = (kwargs.get("user_email") or "").lower()
        if needle:
            items = [i for i in items if needle in i.user_email.lower()]
        return AdminConversationListResponse(items=items, total=len(items), page=1, page_size=50)

    monkeypatch.setattr(
        "app.api.routes.admin.conversations.list_admin_conversations", fake_list
    )

    response = admin_client.get(
        "/api/v1/admin/conversations",
        params={"user_email": "alice"},
        headers=_admin_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["user_email"] == "alice@x.com"


def test_list_conversations_non_admin_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get(
        "/api/v1/admin/conversations",
        headers=_non_admin_auth(),
    )
    assert response.status_code == 404


def test_get_conversation_detail_returns_messages_in_order(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_id = uuid4()

    async def fake_detail(_db: Any, conversation_id: UUID) -> AdminConversationDetail:
        assert conversation_id == target_id
        base = _make_conv_item()
        return AdminConversationDetail(
            id=target_id,
            started_at=base.started_at,
            last_activity_at=base.last_activity_at,
            status=base.status,
            channel=base.channel,
            visitor_id=base.visitor_id,
            agent_id=base.agent_id,
            agent_name=base.agent_name,
            user_id=base.user_id,
            user_email=base.user_email,
            customer_message_count=base.customer_message_count,
            assistant_message_count=base.assistant_message_count,
            tool_call_count=base.tool_call_count,
            total_input_tokens=base.total_input_tokens,
            total_output_tokens=base.total_output_tokens,
            fallback_used=base.fallback_used,
            latest_message_preview=base.latest_message_preview,
            closed_at=None,
            metadata={"fallback_used": False},
            messages=[
                AdminMessageDTO(
                    id=uuid4(),
                    role="user",
                    content="hi",
                    tool_name=None,
                    tool_call_id=None,
                    tool_call_payload={},
                    tool_result_payload={},
                    model=None,
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=None,
                    metadata={},
                    created_at=datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc),
                ),
                AdminMessageDTO(
                    id=uuid4(),
                    role="assistant",
                    content="hello",
                    tool_name=None,
                    tool_call_id=None,
                    tool_call_payload={},
                    tool_result_payload={},
                    model="gpt-4o-mini",
                    input_tokens=20,
                    output_tokens=4,
                    latency_ms=120,
                    metadata={},
                    created_at=datetime(2026, 5, 5, 10, 0, 1, tzinfo=timezone.utc),
                ),
            ],
            truncated=False,
            total_message_count=2,
            transcript_message_cap=1000,
        )

    monkeypatch.setattr(
        "app.api.routes.admin.conversations.get_admin_conversation_detail", fake_detail
    )

    response = admin_client.get(
        f"/api/v1/admin/conversations/{target_id}",
        headers=_admin_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is False
    assert body["total_message_count"] == 2
    assert body["transcript_message_cap"] == 1000
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][1]["model"] == "gpt-4o-mini"


def test_get_conversation_detail_non_admin_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get(
        f"/api/v1/admin/conversations/{uuid4()}",
        headers=_non_admin_auth(),
    )
    assert response.status_code == 404
