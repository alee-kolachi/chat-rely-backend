"""Routing-layer tests for /api/v1/admin/knowledge/* and /api/v1/admin/indexing/jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.domains.admin.schemas import (
    AdminIndexingJobListResponse,
    AdminIndexingJobRow,
    AdminKnowledgeChunkPreview,
    AdminKnowledgeSourceDetail,
    AdminKnowledgeSourceListResponse,
    AdminKnowledgeSourceRow,
)
from tests._admin_test_helpers import admin_auth, admin_client, non_admin_auth


__all__ = ["admin_client"]


def _make_source_row(status: str = "ready") -> AdminKnowledgeSourceRow:
    return AdminKnowledgeSourceRow(
        id=uuid4(),
        user_id=uuid4(),
        user_email="alice@x.com",
        agent_id=uuid4(),
        agent_name="Support Bot",
        type="website",
        title="Pricing page",
        status=status,
        source_url="https://example.com/pricing",
        last_indexed_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        error_message="boom" if status == "failed" else None,
        chunks_count=12,
        chunks_total_tokens=4800,
        created_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )


def _make_job_row(status: str = "queued") -> AdminIndexingJobRow:
    return AdminIndexingJobRow(
        id=uuid4(),
        user_id=uuid4(),
        user_email="alice@x.com",
        agent_id=uuid4(),
        agent_name="Support Bot",
        knowledge_source_id=uuid4(),
        knowledge_source_title="Pricing page",
        status=status,
        attempt=1,
        triggered_by="system",
        error_message=None,
        started_at=None,
        finished_at=None,
        duration_ms=None,
        created_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )


def test_list_knowledge_sources_filters_by_status(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_list(_db: Any, **kwargs: Any) -> AdminKnowledgeSourceListResponse:
        captured.update(kwargs)
        return AdminKnowledgeSourceListResponse(
            items=[_make_source_row("failed")],
            total=1,
            page=1,
            page_size=50,
        )

    monkeypatch.setattr(
        "app.api.routes.admin.knowledge.list_admin_knowledge_sources", fake_list
    )

    response = admin_client.get(
        "/api/v1/admin/knowledge/sources",
        params={"status": "failed", "type": "website"},
        headers=admin_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["error_message"] == "boom"
    assert captured["status"] == "failed"
    assert captured["type_"] == "website"


def test_get_knowledge_source_detail_returns_chunks_and_jobs(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = uuid4()

    async def fake_detail(_db: Any, source_id: UUID) -> AdminKnowledgeSourceDetail:
        assert source_id == target
        return AdminKnowledgeSourceDetail(
            id=target,
            user_id=uuid4(),
            user_email="alice@x.com",
            agent_id=uuid4(),
            agent_name="Support Bot",
            type="file",
            title="manual.pdf",
            status="ready",
            source_url=None,
            last_indexed_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            error_message=None,
            chunks_count=2,
            chunks_total_tokens=400,
            created_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
            storage_bucket="docs",
            storage_path="user/agent/manual.pdf",
            metadata={"checksum": "abc"},
            recent_jobs=[_make_job_row("succeeded")],
            sample_chunks=[
                AdminKnowledgeChunkPreview(
                    id=uuid4(),
                    chunk_index=0,
                    token_count=200,
                    content_preview="Welcome to the manual...",
                    created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                )
            ],
        )

    monkeypatch.setattr(
        "app.api.routes.admin.knowledge.get_admin_knowledge_source_detail", fake_detail
    )

    response = admin_client.get(
        f"/api/v1/admin/knowledge/sources/{target}", headers=admin_auth()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "manual.pdf"
    assert body["sample_chunks"][0]["content_preview"].startswith("Welcome")
    assert body["recent_jobs"][0]["status"] == "succeeded"


def test_list_indexing_jobs_passes_status_list(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_list(_db: Any, **kwargs: Any) -> AdminIndexingJobListResponse:
        captured.update(kwargs)
        return AdminIndexingJobListResponse(
            items=[_make_job_row("queued"), _make_job_row("running")],
            total=2,
            page=1,
            page_size=50,
        )

    monkeypatch.setattr(
        "app.api.routes.admin.knowledge.list_admin_indexing_jobs", fake_list
    )

    response = admin_client.get(
        "/api/v1/admin/indexing/jobs?status=queued&status=running",
        headers=admin_auth(),
    )
    assert response.status_code == 200
    assert captured["statuses"] == ["queued", "running"]


def test_indexing_jobs_non_admin_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get("/api/v1/admin/indexing/jobs", headers=non_admin_auth())
    assert response.status_code == 404
