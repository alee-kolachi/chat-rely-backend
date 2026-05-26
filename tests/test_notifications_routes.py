from typing import Any
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError
import app.api.routes.notifications as notifications_routes
from app.domains.notifications.schemas import NotificationDTO


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer good-token"}


class _DummyVerifier:
    def verify_token(self, token: str) -> dict[str, str]:
        if token == "good-token":
            return {"sub": "00000000-0000-0000-0000-000000000123"}
        raise AppError("auth.unauthorized", "bad token", status_code=401)


def _patch_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())


def test_list_notifications(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    now = datetime.now(tz=UTC)
    n = NotificationDTO(
        id=uuid4(),
        kind="escalation",
        title="Escalated",
        body="Body",
        href="/conversations",
        metadata={},
        read_at=None,
        created_at=now,
    )

    async def _list(*_: Any, **__: Any) -> tuple[list[NotificationDTO], int]:
        return [n], 1

    monkeypatch.setattr(notifications_routes, "list_notifications", _list)
    r = client.get("/api/v1/notifications", headers=_auth_header())
    assert r.status_code == 200
    body = r.json()
    assert body["unread_count"] == 1
    assert len(body["notifications"]) == 1
    assert body["notifications"][0]["title"] == "Escalated"
    assert body["notifications"][0]["href"] == "/conversations"


def test_mark_notifications_read(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    called: dict[str, Any] = {}

    async def _mark(
        db: Any,
        *,
        user_id: UUID,
        notification_ids: list[UUID] | None,
        mark_all: bool,
    ) -> int:
        called["notification_ids"] = notification_ids
        called["mark_all"] = mark_all
        return 2

    monkeypatch.setattr(notifications_routes, "mark_notifications_read", _mark)

    r = client.post(
        "/api/v1/notifications/read",
        headers=_auth_header(),
        json={"notification_ids": ["00000000-0000-4000-8000-000000000001"]},
    )
    assert r.status_code == 200
    assert r.json() == {"updated": 2}
    assert called["mark_all"] is False
    assert len(called["notification_ids"]) == 1

    r2 = client.post(
        "/api/v1/notifications/read",
        headers=_auth_header(),
        json={"all": True},
    )
    assert r2.status_code == 200
    assert called["mark_all"] is True
