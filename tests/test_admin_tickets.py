"""Routing-layer tests for /api/v1/admin/tickets."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.domains.admin.schemas import (
    AdminConversationListItem,
    AdminTicketDetail,
    AdminTicketListItem,
    AdminTicketListResponse,
)
from tests._admin_test_helpers import admin_auth, admin_client, non_admin_auth


__all__ = ["admin_client"]


def _make_ticket_item(status: str = "open") -> AdminTicketListItem:
    return AdminTicketListItem(
        id=uuid4(),
        user_id=uuid4(),
        user_email="alice@x.com",
        agent_id=uuid4(),
        agent_name="Support Bot",
        conversation_id=uuid4(),
        status=status,
        priority="medium",
        subject="Refund request",
        customer_email="cust@x.com",
        external_provider=None,
        external_id=None,
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
    )


def test_list_tickets_filters_by_status(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_list(_db: Any, **kwargs: Any) -> AdminTicketListResponse:
        captured.update(kwargs)
        return AdminTicketListResponse(
            items=[_make_ticket_item("open")],
            total=1,
            page=1,
            page_size=50,
        )

    monkeypatch.setattr("app.api.routes.admin.tickets.list_admin_tickets", fake_list)

    response = admin_client.get(
        "/api/v1/admin/tickets",
        params={"status": "open", "priority": "high"},
        headers=admin_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "open"
    assert captured["status"] == "open"
    assert captured["priority"] == "high"


def test_get_ticket_detail_includes_linked_conversation(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = uuid4()
    conv_id = uuid4()

    async def fake_detail(_db: Any, ticket_id: UUID) -> AdminTicketDetail:
        assert ticket_id == target
        return AdminTicketDetail(
            id=target,
            user_id=uuid4(),
            user_email="alice@x.com",
            agent_id=uuid4(),
            agent_name="Support Bot",
            conversation_id=conv_id,
            status="open",
            priority="medium",
            subject="Refund",
            customer_email="cust@x.com",
            external_provider=None,
            external_id=None,
            metadata={"source": "widget"},
            created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
            conversation=AdminConversationListItem(
                id=conv_id,
                started_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                last_activity_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                status="open",
                channel="widget",
                visitor_id="v1",
                agent_id=uuid4(),
                agent_name="Support Bot",
                user_id=uuid4(),
                user_email="alice@x.com",
                customer_message_count=2,
                assistant_message_count=2,
                tool_call_count=0,
                total_input_tokens=200,
                total_output_tokens=180,
                fallback_used=False,
                latest_message_preview="Hi",
            ),
        )

    monkeypatch.setattr("app.api.routes.admin.tickets.get_admin_ticket_detail", fake_detail)

    response = admin_client.get(f"/api/v1/admin/tickets/{target}", headers=admin_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["conversation"]["id"] == str(conv_id)
    assert body["conversation"]["agent_name"] == "Support Bot"
    assert body["metadata"] == {"source": "widget"}


def test_list_tickets_non_admin_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get("/api/v1/admin/tickets", headers=non_admin_auth())
    assert response.status_code == 404
