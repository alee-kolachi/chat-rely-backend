from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.domains.knowledge.schemas import IndexJobDTO, KnowledgeSourceDTO
from app.domains.onboarding.schemas import OnboardingStartResponse, OnboardingStatusResponse, OnboardingWebsiteResponse


class _DummyVerifier:
    def verify_token(self, token: str) -> dict[str, str]:
        if token == "good-token":
            return {"sub": "00000000-0000-0000-0000-000000000123"}
        raise AppError(code="auth.unauthorized", message="bad token", status_code=401)


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer good-token"}


def _patch_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())


def test_onboarding_start(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _start(*_: Any, **__: Any) -> OnboardingStartResponse:
        return OnboardingStartResponse(agent_id=uuid4(), current_step=1)

    monkeypatch.setattr("app.api.routes.onboarding.start_onboarding", _start)
    response = client.post("/api/v1/onboarding/start", headers=_auth_header(), json={"name": "Sales Agent"})
    assert response.status_code == 200
    assert response.json()["current_step"] == 1


def test_onboarding_website_enqueue(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    async def _submit(*_: Any, **__: Any) -> OnboardingWebsiteResponse:
        return OnboardingWebsiteResponse(
            source_id=uuid4(),
            job_id=uuid4(),
            status="succeeded",
            website_url="https://example.com",
            pages=[],
            preview_image_url=None,
        )

    monkeypatch.setattr("app.api.routes.onboarding.submit_website", _submit)
    response = client.post(
        "/api/v1/onboarding/website",
        headers=_auth_header(),
        json={
            "agent_id": str(uuid4()),
            "website_url": "https://example.com",
            "title": "Example",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["website_url"] == "https://example.com"
    assert body["pages"] == []


def test_onboarding_status(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    agent_id = uuid4()

    async def _status(*_: Any, **__: Any) -> OnboardingStatusResponse:
        return OnboardingStatusResponse(
            agent_id=agent_id,
            current_step=3,
            progress_pct=55,
            session_status="in_progress",
            website_url="https://example.com",
            website_title="Example",
            preview_asset={"source_url": "https://example.com", "capture_status": "pending"},
            indexing_job={"status": "running", "pages_total": 5, "pages_processed": 2},
            checklist=[],
        )

    monkeypatch.setattr("app.api.routes.onboarding.get_onboarding_status", _status)
    response = client.get(f"/api/v1/onboarding/status?agent_id={agent_id}", headers=_auth_header())
    assert response.status_code == 200
    assert response.json()["website_url"] == "https://example.com"


@pytest.mark.asyncio
async def test_worker_loop_processes_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import indexing_worker

    calls: dict[str, int] = {"processed": 0}
    job_id = uuid4()
    user_id = uuid4()

    async def _fetch_once() -> tuple[Any, Any] | None:
        if calls["processed"] == 0:
            return job_id, user_id
        return None

    async def _process(*_: Any, **__: Any) -> None:
        calls["processed"] += 1

    async def _sleep(_: float) -> None:
        raise RuntimeError("stop-loop")

    monkeypatch.setattr(indexing_worker, "_fetch_next_job_id", _fetch_once)
    monkeypatch.setattr(indexing_worker, "process_indexing_job", _process)
    monkeypatch.setattr(indexing_worker, "init_engine", lambda *_: None)
    monkeypatch.setattr(indexing_worker, "init_session_factory", lambda: None)
    monkeypatch.setattr(indexing_worker, "get_settings", lambda: type("S", (), {})())
    monkeypatch.setattr(indexing_worker.asyncio, "sleep", _sleep)

    with pytest.raises(RuntimeError, match="stop-loop"):
        await indexing_worker.run_worker_loop(poll_interval_seconds=0.01)
    assert calls["processed"] == 1


@pytest.mark.asyncio
async def test_worker_loop_surrogate_failure_on_uncaught_process_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import indexing_worker

    job_id = uuid4()
    user_id = uuid4()
    surrogate_calls: dict[str, int] = {"n": 0}
    fetch_calls: dict[str, int] = {"n": 0}

    async def _fetch_twice() -> tuple[Any, Any] | None:
        fetch_calls["n"] += 1
        if fetch_calls["n"] == 1:
            return job_id, user_id
        return None

    async def _process_raises(*_: Any, **__: Any) -> None:
        raise RuntimeError("simulated indexing bug")

    async def _surrogate(*_: Any, **__: Any) -> None:
        surrogate_calls["n"] += 1

    async def _sleep(_: float) -> None:
        raise RuntimeError("stop-loop")

    monkeypatch.setattr(indexing_worker, "_fetch_next_job_id", _fetch_twice)
    monkeypatch.setattr(indexing_worker, "process_indexing_job", _process_raises)
    monkeypatch.setattr(indexing_worker, "record_worker_indexing_surrogate_failure", _surrogate)
    monkeypatch.setattr(indexing_worker, "init_engine", lambda *_: None)
    monkeypatch.setattr(indexing_worker, "init_session_factory", lambda: None)
    monkeypatch.setattr(indexing_worker, "get_settings", lambda: type("S", (), {})())
    monkeypatch.setattr(indexing_worker.asyncio, "sleep", _sleep)

    with pytest.raises(RuntimeError, match="stop-loop"):
        await indexing_worker.run_worker_loop(poll_interval_seconds=0.01)
    assert surrogate_calls["n"] == 1


def test_knowledge_index_route_returns_queued_status(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)

    source = KnowledgeSourceDTO.model_validate(
        {
            "id": str(uuid4()),
            "agent_id": str(uuid4()),
            "user_id": "00000000-0000-0000-0000-000000000123",
            "type": "website",
            "title": "Docs",
            "status": "indexing",
            "source_url": "https://example.com",
            "storage_bucket": None,
            "storage_path": None,
            "metadata": {},
            "error_message": None,
            "last_indexed_at": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    job = IndexJobDTO.model_validate(
        {
            "id": str(uuid4()),
            "knowledge_source_id": str(source.id),
            "agent_id": str(source.agent_id),
            "user_id": "00000000-0000-0000-0000-000000000123",
            "status": "queued",
            "attempt": 1,
            "triggered_by": "api",
            "error_message": None,
            "started_at": None,
            "finished_at": None,
            "phase": "queued",
            "pages_total": 0,
            "pages_processed": 0,
            "chunks_total": 0,
            "chunks_embedded": 0,
            "progress_pct": 0,
            "metrics": {},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )

    async def _index(*_: Any, **__: Any) -> tuple[KnowledgeSourceDTO, IndexJobDTO]:
        return source, job

    async def _load(_db: Any, source_id: UUID, _user_id: UUID) -> KnowledgeSourceDTO:
        return source.model_copy(update={"id": source_id})

    monkeypatch.setattr("app.api.routes.knowledge._load_source", _load)
    monkeypatch.setattr("app.api.routes.knowledge.index_website_source", _index)
    response = client.post(f"/api/v1/knowledge/sources/{source.id}/index", headers=_auth_header())
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "queued"
