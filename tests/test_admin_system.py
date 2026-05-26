"""Routing-layer tests for /api/v1/admin/system/health."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domains.admin.schemas import (
    AdminPricingStatus,
    AdminSystemHealth,
    AdminWorkerStatus,
)
from tests._admin_test_helpers import admin_auth, admin_client, non_admin_auth


__all__ = ["admin_client"]


def _make_health(
    *,
    unknown: list[str] | None = None,
    db_ready: bool = True,
) -> AdminSystemHealth:
    return AdminSystemHealth(
        app_name="ChatRely Backend",
        app_version="0.1.0",
        app_env="development",
        database_ready=db_ready,
        database_latency_ms=4,
        workers=AdminWorkerStatus(
            indexing_last_success_at=datetime(2026, 5, 8, 10, tzinfo=timezone.utc),
            indexing_last_attempt_at=datetime(2026, 5, 8, 11, tzinfo=timezone.utc),
            indexing_queue_depth=2,
            maintenance_last_computed_at=datetime(2026, 5, 8, 9, tzinfo=timezone.utc),
        ),
        pricing_configured=AdminPricingStatus(
            llm_input_models=["gpt-4o-mini"],
            llm_output_models=["gpt-4o-mini"],
            embedding_models=["text-embedding-3-small"],
            embedding_active_model="text-embedding-3-small",
            embedding_active_model_priced=True,
            unknown_models_in_messages=unknown or [],
        ),
    )


def test_system_health_shape(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_health(_db: Any) -> AdminSystemHealth:
        return _make_health()

    monkeypatch.setattr(
        "app.api.routes.admin.system.get_admin_system_health", fake_health
    )

    response = admin_client.get("/api/v1/admin/system/health", headers=admin_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["app_version"] == "0.1.0"
    assert body["database_ready"] is True
    assert body["database_latency_ms"] == 4
    assert body["workers"]["indexing_queue_depth"] == 2
    assert body["pricing_configured"]["llm_input_models"] == ["gpt-4o-mini"]


def test_system_health_surfaces_unknown_models(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_health(_db: Any) -> AdminSystemHealth:
        return _make_health(unknown=["weird-model"])

    monkeypatch.setattr(
        "app.api.routes.admin.system.get_admin_system_health", fake_health
    )

    response = admin_client.get("/api/v1/admin/system/health", headers=admin_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["pricing_configured"]["unknown_models_in_messages"] == ["weird-model"]


def test_system_health_db_unready(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_health(_db: Any) -> AdminSystemHealth:
        return _make_health(db_ready=False)

    monkeypatch.setattr(
        "app.api.routes.admin.system.get_admin_system_health", fake_health
    )

    response = admin_client.get("/api/v1/admin/system/health", headers=admin_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["database_ready"] is False


def test_system_health_non_admin_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get("/api/v1/admin/system/health", headers=non_admin_auth())
    assert response.status_code == 404
