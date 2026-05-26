"""Routing-layer tests for /api/v1/admin/plans.

Phase 4 acceptance criterion: returns inactive plans (which the user-facing
/api/v1/plans/public hides), with `subscriptions_count` per plan.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.domains.admin.schemas import AdminPlanListResponse, AdminPlanRow
from tests._admin_test_helpers import admin_auth, admin_client, non_admin_auth


__all__ = ["admin_client"]


def _make_plan(
    slug: str,
    *,
    is_active: bool = True,
    subs: int = 0,
    public_on_pricing_page: bool = True,
) -> AdminPlanRow:
    return AdminPlanRow(
        id=uuid4(),
        slug=slug,
        name=slug.title(),
        monthly_price_cents=2900 if slug != "free" else 0,
        included_conversations=100,
        overage_conversation_cents=10,
        max_agents=3,
        features={"human_escalation_enabled": True},
        throttle_policy={"soft_threshold_pct": 80},
        is_active=is_active,
        public_on_pricing_page=public_on_pricing_page,
        sort_order=10,
        subscriptions_count=subs,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_list_plans_returns_inactive_plans_and_counts(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_list(_db: Any) -> AdminPlanListResponse:
        return AdminPlanListResponse(
            items=[
                _make_plan("free", is_active=True, subs=20),
                _make_plan("standard", is_active=True, subs=5),
                _make_plan("legacy_starter", is_active=False, subs=2, public_on_pricing_page=False),
                _make_plan("scale", is_active=True, subs=1, public_on_pricing_page=False),
            ]
        )

    monkeypatch.setattr("app.api.routes.admin.plans.list_admin_plans", fake_list)

    response = admin_client.get("/api/v1/admin/plans", headers=admin_auth())
    assert response.status_code == 200
    body = response.json()
    slugs = [item["slug"] for item in body["items"]]
    assert "legacy_starter" in slugs
    legacy = next(item for item in body["items"] if item["slug"] == "legacy_starter")
    assert legacy["is_active"] is False
    assert legacy["subscriptions_count"] == 2
    standard = next(item for item in body["items"] if item["slug"] == "standard")
    assert standard["features"] == {"human_escalation_enabled": True}
    assert standard["throttle_policy"] == {"soft_threshold_pct": 80}
    assert standard["public_on_pricing_page"] is True
    assert standard["sort_order"] == 10
    scale = next(item for item in body["items"] if item["slug"] == "scale")
    assert scale["public_on_pricing_page"] is False


def test_list_plans_non_admin_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get("/api/v1/admin/plans", headers=non_admin_auth())
    assert response.status_code == 404
