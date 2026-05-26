"""Routing-layer tests for /api/v1/admin/agents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.domains.admin.schemas import (
    AdminAgentActionRow,
    AdminAgentDetail,
    AdminAgentListItem,
    AdminAgentListResponse,
)
from tests._admin_test_helpers import admin_auth, admin_client, non_admin_auth


__all__ = ["admin_client"]


def _make_agent_item(name: str = "Support Bot") -> AdminAgentListItem:
    return AdminAgentListItem(
        id=uuid4(),
        user_id=uuid4(),
        user_email="alice@x.com",
        name=name,
        slug=name.lower().replace(" ", "-"),
        status="active",
        model="gpt-4o-mini",
        conversations_total=120,
        conversations_mtd=15,
        knowledge_sources_count=3,
        actions_enabled_count=2,
        created_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
        archived_at=None,
    )


def test_list_agents_passes_filters(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_list(_db: Any, **kwargs: Any) -> AdminAgentListResponse:
        captured.update(kwargs)
        return AdminAgentListResponse(
            items=[_make_agent_item("Alpha"), _make_agent_item("Beta")],
            total=2,
            page=int(kwargs.get("page", 1)),
            page_size=int(kwargs.get("page_size", 50)),
        )

    monkeypatch.setattr("app.api.routes.admin.agents.list_admin_agents", fake_list)

    response = admin_client.get(
        "/api/v1/admin/agents",
        params={"user_email": "ali", "status": "active", "model": "gpt-4o-mini"},
        headers=admin_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert captured["user_email"] == "ali"
    assert captured["status"] == "active"
    assert captured["model"] == "gpt-4o-mini"


def test_list_agents_rejects_unknown_sort(admin_client: TestClient) -> None:
    response = admin_client.get(
        "/api/v1/admin/agents",
        params={"sort_by": "drop table"},
        headers=admin_auth(),
    )
    assert response.status_code == 422


def test_get_agent_detail_returns_full_shape(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = uuid4()

    async def fake_detail(_db: Any, agent_id: UUID) -> AdminAgentDetail:
        assert agent_id == target
        return AdminAgentDetail(
            id=target,
            user_id=uuid4(),
            user_email="alice@x.com",
            name="Support Bot",
            slug="support",
            status="active",
            model="gpt-4o-mini",
            conversations_total=120,
            conversations_mtd=15,
            knowledge_sources_count=3,
            actions_enabled_count=1,
            created_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
            archived_at=None,
            system_prompt="You are a helpful agent.",
            behavior_settings={"temperature": 0.2},
            public_key="pk_abc123",
            knowledge_sources=[],
            actions=[
                AdminAgentActionRow(
                    id=uuid4(),
                    action_key="shopify.lookup_order",
                    enabled=True,
                    config={"foo": "bar"},
                    safety_policy={},
                    created_at=datetime(2026, 1, 6, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 1, 6, tzinfo=timezone.utc),
                )
            ],
            recent_conversations=[],
        )

    monkeypatch.setattr("app.api.routes.admin.agents.get_admin_agent_detail", fake_detail)

    response = admin_client.get(f"/api/v1/admin/agents/{target}", headers=admin_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Support Bot"
    assert body["public_key"] == "pk_abc123"
    assert body["system_prompt"] == "You are a helpful agent."
    assert body["actions"][0]["action_key"] == "shopify.lookup_order"
    assert body["actions"][0]["config"] == {"foo": "bar"}


def test_list_agents_non_admin_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get("/api/v1/admin/agents", headers=non_admin_auth())
    assert response.status_code == 404
