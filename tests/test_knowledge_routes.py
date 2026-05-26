from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.domains.knowledge.schemas import (
    IndexJobDTO,
    KnowledgeSourceDTO,
)


class _DummyVerifier:
    def verify_token(self, token: str) -> dict[str, str]:
        if token == "good-token":
            return {"sub": "00000000-0000-0000-0000-000000000123"}
        raise AppError("auth.unauthorized", "bad token", status_code=401)


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer good-token"}


def _source(source_type: str = "website") -> KnowledgeSourceDTO:
    return KnowledgeSourceDTO.model_validate(
        {
            "id": str(uuid4()),
            "agent_id": "00000000-0000-0000-0000-000000000888",
            "user_id": "00000000-0000-0000-0000-000000000123",
            "type": source_type,
            "title": "Docs",
            "status": "pending",
            "source_url": "https://example.com" if source_type == "website" else None,
            "storage_bucket": "knowledge-files" if source_type == "file" else None,
            "storage_path": "user/file.pdf" if source_type == "file" else None,
            "metadata": {},
            "error_message": None,
            "last_indexed_at": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )


def _job(status: str = "succeeded") -> IndexJobDTO:
    return IndexJobDTO.model_validate(
        {
            "id": str(uuid4()),
            "knowledge_source_id": str(uuid4()),
            "agent_id": "00000000-0000-0000-0000-000000000888",
            "user_id": "00000000-0000-0000-0000-000000000123",
            "status": status,
            "attempt": 1,
            "triggered_by": "api",
            "error_message": None,
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:00:10Z",
            "metrics": {"chunk_count": 3},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )


def _patch_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())


def _patch_load_website_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Index routes call _load_source before the per-type indexer; avoid needing a real DB row."""

    async def _load(_db: Any, source_id: UUID, _user_id: UUID) -> KnowledgeSourceDTO:
        return _source("website").model_copy(update={"id": source_id})

    monkeypatch.setattr("app.api.routes.knowledge._load_source", _load)


def test_create_website_source(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _create(*_: Any, **__: Any) -> KnowledgeSourceDTO:
        return _source("website")

    monkeypatch.setattr("app.api.routes.knowledge.create_source", _create)
    response = client.post(
        "/api/v1/knowledge/sources",
        headers=_auth_header(),
        json={
            "agent_id": "00000000-0000-0000-0000-000000000888",
            "type": "website",
            "title": "Docs",
            "source_url": "https://example.com",
        },
    )
    assert response.status_code == 200
    assert response.json()["source"]["type"] == "website"


def test_create_file_source_metadata_only(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _create(*_: Any, **__: Any) -> KnowledgeSourceDTO:
        return _source("file")

    monkeypatch.setattr("app.api.routes.knowledge.create_source", _create)
    response = client.post(
        "/api/v1/knowledge/sources",
        headers=_auth_header(),
        json={
            "agent_id": "00000000-0000-0000-0000-000000000888",
            "type": "file",
            "title": "Guide PDF",
            "storage_bucket": "knowledge-files",
            "storage_path": "user/guide.pdf",
        },
    )
    assert response.status_code == 200
    assert response.json()["source"]["type"] == "file"


def test_list_sources(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _list(*_: Any, **__: Any) -> list[KnowledgeSourceDTO]:
        return [_source("website"), _source("file")]

    monkeypatch.setattr("app.api.routes.knowledge.list_sources", _list)
    response = client.get("/api/v1/knowledge/sources", headers=_auth_header())
    assert response.status_code == 200
    assert len(response.json()["sources"]) == 2


def test_index_website_source_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    _patch_load_website_source(monkeypatch)

    async def _index(*_: Any, **__: Any) -> tuple[KnowledgeSourceDTO, IndexJobDTO]:
        return _source("website"), _job("succeeded")

    monkeypatch.setattr("app.api.routes.knowledge.index_website_source", _index)
    response = client.post(f"/api/v1/knowledge/sources/{uuid4()}/index", headers=_auth_header())
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "succeeded"


def test_indexing_failure_surfaces_standard_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    _patch_load_website_source(monkeypatch)

    async def _index(*_: Any, **__: Any) -> tuple[KnowledgeSourceDTO, IndexJobDTO]:
        raise AppError("knowledge.indexing_failed", "Indexing job failed", status_code=500)

    monkeypatch.setattr("app.api.routes.knowledge.index_website_source", _index)
    response = client.post(f"/api/v1/knowledge/sources/{uuid4()}/index", headers=_auth_header())
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "knowledge.indexing_failed"


def test_list_indexing_jobs(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _jobs(*_: Any, **__: Any) -> list[IndexJobDTO]:
        return [_job("running"), _job("succeeded")]

    monkeypatch.setattr("app.api.routes.knowledge.get_jobs", _jobs)
    response = client.get(f"/api/v1/knowledge/sources/{uuid4()}/indexing-jobs", headers=_auth_header())
    assert response.status_code == 200
    assert len(response.json()["jobs"]) == 2


def test_reindex_replaces_chunks_semantic(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    _patch_load_website_source(monkeypatch)
    calls: dict[str, int] = {"index": 0}

    async def _index(*_: Any, **__: Any) -> tuple[KnowledgeSourceDTO, IndexJobDTO]:
        calls["index"] += 1
        return _source("website"), _job("succeeded")

    monkeypatch.setattr("app.api.routes.knowledge.index_website_source", _index)
    source_id = uuid4()
    first = client.post(f"/api/v1/knowledge/sources/{source_id}/index", headers=_auth_header())
    second = client.post(f"/api/v1/knowledge/sources/{source_id}/index", headers=_auth_header())
    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["index"] == 2

