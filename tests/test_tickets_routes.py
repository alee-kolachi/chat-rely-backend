from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError
import app.api.routes.tickets as tickets_routes
from app.domains.tickets.schemas import TicketDTO


class _DummyVerifier:
    def verify_token(self, token: str) -> dict[str, str]:
        if token == "good-token":
            return {"sub": "00000000-0000-0000-0000-000000000123"}
        raise AppError("auth.unauthorized", "bad token", status_code=401)


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer good-token"}


def _patch_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())


def test_list_tickets_empty(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _list(
        db: Any,
        *,
        user_id: Any,
        agent_id: Any = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TicketDTO], int]:
        return [], 0

    monkeypatch.setattr(tickets_routes, "list_tickets", _list)
    r = client.get("/api/v1/tickets", headers=_auth_header())
    assert r.status_code == 200
    assert r.json() == {"tickets": [], "total": 0}


def test_get_ticket_not_found(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _get(*_: Any, **__: Any) -> TicketDTO:
        raise AppError(code="ticket.not_found", message="Ticket not found", status_code=404)

    monkeypatch.setattr(tickets_routes, "get_ticket", _get)
    r = client.get(f"/api/v1/tickets/{uuid4()}", headers=_auth_header())
    assert r.status_code == 404
