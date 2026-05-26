"""Routing-layer tests for /api/v1/admin/overview.

The service is monkeypatched so we don't need DB seed data; we're just confirming the
single-call aggregator surfaces correctly through the route, and that non-admins get
404 (cross-cutting Phase 4 acceptance criterion).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domains.admin.schemas import (
    AdminOverview,
    AdminOverviewKpis,
    AdminWorkerStatus,
)
from tests._admin_test_helpers import admin_auth, admin_client, non_admin_auth


__all__ = ["admin_client"]  # re-exported so pytest discovers the fixture in this file


def _make_overview() -> AdminOverview:
    return AdminOverview(
        kpis=AdminOverviewKpis(
            total_users=42,
            signups_today=2,
            signups_last_7d=8,
            active_subscriptions=12,
            mrr_usd=348.0,
            conversations_today=17,
            conversations_mtd=210,
            indexing_queue_depth=3,
            indexing_failed_24h=1,
        ),
        recent_signups=[],
        recent_conversations=[],
        recent_stripe_events=[],
        recent_indexing_failures=[],
        workers=AdminWorkerStatus(
            indexing_last_success_at=datetime(2026, 5, 8, 10, tzinfo=timezone.utc),
            indexing_last_attempt_at=datetime(2026, 5, 8, 11, tzinfo=timezone.utc),
            indexing_queue_depth=3,
            maintenance_last_computed_at=datetime(2026, 5, 8, 9, tzinfo=timezone.utc),
        ),
        llm_cost_mtd_usd=12.5,
        embedding_cost_mtd_usd=0.5,
        revenue_mtd_usd=348.0,
        gross_margin_mtd_usd=335.0,
        gross_margin_mtd_pct=96.26,
        pricing_unknown_models=["weird-model-1"],
    )


def test_admin_overview_returns_kpis_and_workers(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_get(_db: Any) -> AdminOverview:
        return _make_overview()

    monkeypatch.setattr("app.api.routes.admin.overview.get_admin_overview", fake_get)

    response = admin_client.get("/api/v1/admin/overview", headers=admin_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["kpis"]["total_users"] == 42
    assert body["kpis"]["mrr_usd"] == 348.0
    assert body["kpis"]["indexing_queue_depth"] == 3
    assert body["workers"]["indexing_queue_depth"] == 3
    assert body["pricing_unknown_models"] == ["weird-model-1"]
    assert body["llm_cost_mtd_usd"] == 12.5


def test_admin_overview_non_admin_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get("/api/v1/admin/overview", headers=non_admin_auth())
    assert response.status_code == 404


def test_admin_overview_no_token_returns_401(admin_client: TestClient) -> None:
    response = admin_client.get("/api/v1/admin/overview")
    assert response.status_code == 401
