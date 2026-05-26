import json
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.domains.conversations.schemas import ConversationDTO, MessageDTO
from app.domains.runtime.schemas import RuntimeChatResponse


class _DummyVerifier:
    def verify_token(self, token: str) -> dict[str, str]:
        if token == "good-token":
            return {"sub": "00000000-0000-0000-0000-000000000123"}
        raise AppError("auth.unauthorized", "bad token", status_code=401)


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer good-token"}


def _conversation(status: str = "open") -> ConversationDTO:
    return ConversationDTO.model_validate(
        {
            "id": str(uuid4()),
            "agent_id": "00000000-0000-0000-0000-000000000888",
            "user_id": "00000000-0000-0000-0000-000000000123",
            "visitor_id": "preview-user",
            "channel": "api",
            "status": status,
            "started_at": "2026-01-01T00:00:00Z",
            "last_activity_at": "2026-01-01T00:00:00Z",
            "closed_at": None,
            "customer_message_count": 1,
            "assistant_message_count": 1,
            "tool_call_count": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "counts_toward_plan": False,
            "metadata": {},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "latest_message_preview": None,
        }
    )


def _message(role: str = "assistant", content: str = "Hello") -> MessageDTO:
    return MessageDTO.model_validate(
        {
            "id": str(uuid4()),
            "conversation_id": str(uuid4()),
            "agent_id": "00000000-0000-0000-0000-000000000888",
            "user_id": "00000000-0000-0000-0000-000000000123",
            "role": role,
            "content": content,
            "tool_name": None,
            "tool_call_id": None,
            "tool_call_payload": {},
            "tool_result_payload": {},
            "model": "gpt-4o-mini",
            "input_tokens": 0,
            "output_tokens": 0,
            "latency_ms": None,
            "metadata": {},
            "created_at": "2026-01-01T00:00:00Z",
        }
    )


def _patch_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())


def test_runtime_chat_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _run_chat(*_: Any, **__: Any) -> RuntimeChatResponse:
        return RuntimeChatResponse.model_validate(
            {
                "conversation_id": str(uuid4()),
                "assistant_message_id": str(uuid4()),
                "response": "Grounded answer",
                "model": "gpt-4o-mini",
                "fallback_used": False,
                "retrieval_count": 3,
                "min_similarity": 0.72,
                "created_at": "2026-01-01T00:00:00Z",
                "retrieval_preview": [{"knowledge_source_id": str(uuid4()), "similarity": 0.84, "snippet": "A"}],
            }
        )

    monkeypatch.setattr("app.api.routes.runtime.agent_run_chat", _run_chat)
    response = client.post(
        "/api/v1/runtime/chat",
        headers=_auth_header(),
        json={
            "agent_id": "00000000-0000-0000-0000-000000000888",
            "message": "What are your refund rules?",
        },
    )
    assert response.status_code == 200
    assert response.json()["fallback_used"] is False


def test_runtime_chat_fallback_used(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _run_chat(*_: Any, **__: Any) -> RuntimeChatResponse:
        return RuntimeChatResponse.model_validate(
            {
                "conversation_id": str(uuid4()),
                "assistant_message_id": str(uuid4()),
                "response": "I am not fully sure based on available information. Please clarify your request.",
                "model": "gpt-4o-mini",
                "fallback_used": True,
                "retrieval_count": 0,
                "min_similarity": 0.72,
                "created_at": "2026-01-01T00:00:00Z",
                "retrieval_preview": [],
            }
        )

    monkeypatch.setattr("app.api.routes.runtime.agent_run_chat", _run_chat)
    response = client.post(
        "/api/v1/runtime/chat",
        headers=_auth_header(),
        json={
            "agent_id": "00000000-0000-0000-0000-000000000888",
            "message": "Unknown topic",
        },
    )
    assert response.status_code == 200
    assert response.json()["fallback_used"] is True
    assert response.json()["retrieval_count"] == 0




def test_list_conversations(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _list(*_: Any, **__: Any) -> list[ConversationDTO]:
        return [_conversation("open"), _conversation("resolved")]

    monkeypatch.setattr("app.api.routes.conversations.list_conversations", _list)
    response = client.get("/api/v1/conversations", headers=_auth_header())
    assert response.status_code == 200
    assert len(response.json()["conversations"]) == 2


def test_conversations_workspace_includes_detail_when_requested(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_auth(monkeypatch)
    cid = uuid4()

    async def _list(*_: Any, **__: Any) -> list[ConversationDTO]:
        return [_conversation("open")]

    async def _get(*_: Any, **__: Any) -> ConversationDTO:
        return _conversation("open")

    async def _msgs(*_: Any, **__: Any) -> list[MessageDTO]:
        return [_message("user", "Hi")]

    monkeypatch.setattr("app.api.routes.conversations.list_conversations", _list)
    monkeypatch.setattr("app.api.routes.conversations.get_conversation", _get)
    monkeypatch.setattr("app.api.routes.conversations.list_messages", _msgs)

    response = client.get(
        f"/api/v1/conversations/workspace?detail_conversation_id={cid}",
        headers=_auth_header(),
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["conversations"]) == 1
    assert body["detail"] is not None
    assert body["detail"]["conversation"]["status"] == "open"
    assert len(body["detail"]["messages"]) == 1


def test_conversations_workspace_omits_detail_when_not_found(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_auth(monkeypatch)

    async def _list(*_: Any, **__: Any) -> list[ConversationDTO]:
        return []

    async def _get(*_: Any, **__: Any) -> ConversationDTO:
        raise AppError(code="conversation.not_found", message="Conversation not found", status_code=404)

    monkeypatch.setattr("app.api.routes.conversations.list_conversations", _list)
    monkeypatch.setattr("app.api.routes.conversations.get_conversation", _get)

    response = client.get(
        f"/api/v1/conversations/workspace?detail_conversation_id={uuid4()}",
        headers=_auth_header(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["conversations"] == []
    assert body["detail"] is None


def test_conversation_not_found_ownership_guard(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _get(*_: Any, **__: Any) -> ConversationDTO:
        raise AppError(code="conversation.not_found", message="Conversation not found", status_code=404)

    monkeypatch.setattr("app.api.routes.conversations.get_conversation", _get)
    response = client.get(f"/api/v1/conversations/{uuid4()}", headers=_auth_header())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation.not_found"


def test_append_message(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _append(*_: Any, **__: Any) -> MessageDTO:
        return _message(role="assistant", content="Agent reply")

    async def _no_email(*_: Any, **__: Any) -> None:
        return None

    marked: dict[str, bool] = {"ok": False}

    async def _mark_engaged(*_: Any, **__: Any) -> None:
        marked["ok"] = True

    monkeypatch.setattr("app.api.routes.conversations.append_message", _append)
    monkeypatch.setattr("app.api.routes.conversations.mark_conversation_operator_engaged", _mark_engaged)
    monkeypatch.setattr("app.api.routes.conversations.maybe_send_ticket_email_reply", _no_email)
    response = client.post(
        f"/api/v1/conversations/{uuid4()}/messages",
        headers=_auth_header(),
        json={"role": "assistant", "content": "Agent reply"},
    )
    assert response.status_code == 200
    assert response.json()["content"] == "Agent reply"
    assert marked["ok"] is True


def test_append_user_message_does_not_mark_operator_engaged(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _append(*_: Any, **__: Any) -> MessageDTO:
        return _message(role="user", content="Hi")

    marked: dict[str, bool] = {"ok": False}

    async def _mark_engaged(*_: Any, **__: Any) -> None:
        marked["ok"] = True

    monkeypatch.setattr("app.api.routes.conversations.append_message", _append)
    monkeypatch.setattr("app.api.routes.conversations.mark_conversation_operator_engaged", _mark_engaged)
    response = client.post(
        f"/api/v1/conversations/{uuid4()}/messages",
        headers=_auth_header(),
        json={"role": "user", "content": "Hi from visitor"},
    )
    assert response.status_code == 200
    assert marked["ok"] is False


def test_status_transition_validation(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _update(*_: Any, **__: Any) -> ConversationDTO:
        return _conversation("resolved")

    async def _messages(*_: Any, **__: Any) -> list[MessageDTO]:
        return [_message()]

    async def _noop_outcome(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr("app.api.routes.conversations.update_conversation_status", _update)
    monkeypatch.setattr("app.api.routes.conversations.list_messages", _messages)
    monkeypatch.setattr("app.api.routes.conversations.analyze_and_persist_outcome", _noop_outcome)
    response = client.patch(
        f"/api/v1/conversations/{uuid4()}",
        headers=_auth_header(),
        json={"status": "resolved"},
    )
    assert response.status_code == 200
    assert response.json()["conversation"]["status"] == "resolved"


def test_status_transition_invalid_value(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    response = client.patch(
        f"/api/v1/conversations/{uuid4()}",
        headers=_auth_header(),
        json={"status": "invalid_status"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request.validation_error"

