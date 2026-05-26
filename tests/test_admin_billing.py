"""Routing-layer tests for /api/v1/admin/billing/*."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.domains.admin.schemas import (
    AdminStripeEventListResponse,
    AdminStripeEventRow,
    AdminSubscriptionListResponse,
    AdminSubscriptionRow,
    AdminUsageSnapshotListResponse,
    AdminUsageSnapshotRow,
)
from tests._admin_test_helpers import admin_auth, admin_client, non_admin_auth


__all__ = ["admin_client"]


def _make_subscription_row(status: str = "active") -> AdminSubscriptionRow:
    return AdminSubscriptionRow(
        id=uuid4(),
        user_id=uuid4(),
        user_email="alice@x.com",
        plan_id=uuid4(),
        plan_slug="standard",
        plan_name="Growth",
        monthly_price_cents=2900,
        status=status,
        provider="stripe",
        provider_customer_id="cus_X",
        provider_subscription_id="sub_X",
        current_period_start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        current_period_end=datetime(2026, 6, 1, tzinfo=timezone.utc),
        cancel_at_period_end=False,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _make_usage_row(throttle: str = "normal") -> AdminUsageSnapshotRow:
    return AdminUsageSnapshotRow(
        id=uuid4(),
        user_id=uuid4(),
        user_email="alice@x.com",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 6, 1),
        included_conversations=100,
        conversations_used=80,
        overage_conversations=0,
        estimated_overage_cents=0,
        projected_conversations=120,
        throttle_tier=throttle,
        included_premium_turns=250,
        premium_turns_used=42,
        last_computed_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )


def test_list_subscriptions_passes_filters(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_list(_db: Any, **kwargs: Any) -> AdminSubscriptionListResponse:
        captured.update(kwargs)
        return AdminSubscriptionListResponse(
            items=[_make_subscription_row("active"), _make_subscription_row("canceled")],
            total=2,
            page=1,
            page_size=50,
        )

    monkeypatch.setattr(
        "app.api.routes.admin.billing.list_admin_subscriptions", fake_list
    )

    response = admin_client.get(
        "/api/v1/admin/billing/subscriptions",
        params={"status": "active", "plan_slug": "standard", "user_email": "ali"},
        headers=admin_auth(),
    )
    assert response.status_code == 200
    assert captured["status"] == "active"
    assert captured["plan_slug"] == "standard"
    assert captured["user_email"] == "ali"


def test_list_usage_snapshots_filters_throttle_tier(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_list(_db: Any, **kwargs: Any) -> AdminUsageSnapshotListResponse:
        captured.update(kwargs)
        return AdminUsageSnapshotListResponse(
            items=[_make_usage_row("strong")],
            total=1,
            page=1,
            page_size=50,
        )

    monkeypatch.setattr(
        "app.api.routes.admin.billing.list_admin_usage_snapshots", fake_list
    )

    response = admin_client.get(
        "/api/v1/admin/billing/usage-snapshots",
        params={"throttle_tier": "strong"},
        headers=admin_auth(),
    )
    assert response.status_code == 200
    assert captured["throttle_tier"] == "strong"
    body = response.json()
    assert body["items"][0]["throttle_tier"] == "strong"
    assert body["items"][0]["included_premium_turns"] == 250
    assert body["items"][0]["premium_turns_used"] == 42


def test_list_stripe_events_orders_newest_first(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_list(_db: Any, **_kwargs: Any) -> AdminStripeEventListResponse:
        return AdminStripeEventListResponse(
            items=[
                AdminStripeEventRow(
                    id=uuid4(),
                    stripe_event_id=f"evt_{i}",
                    event_type="customer.subscription.updated",
                    processed_at=datetime(2026, 5, 8 - i % 5, tzinfo=timezone.utc),
                )
                for i in range(3)
            ],
            total=3,
            page=1,
            page_size=50,
        )

    monkeypatch.setattr(
        "app.api.routes.admin.billing.list_admin_stripe_events", fake_list
    )

    response = admin_client.get(
        "/api/v1/admin/billing/stripe-events", headers=admin_auth()
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3
    assert body["items"][0]["stripe_event_id"] == "evt_0"


def test_billing_routes_non_admin_returns_404(admin_client: TestClient) -> None:
    for path in (
        "/api/v1/admin/billing/subscriptions",
        "/api/v1/admin/billing/usage-snapshots",
        "/api/v1/admin/billing/stripe-events",
    ):
        response = admin_client.get(path, headers=non_admin_auth())
        assert response.status_code == 404, path
