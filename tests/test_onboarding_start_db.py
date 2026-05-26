"""
Integration checks for POST /api/v1/onboarding/start (real DB, real service).

Other onboarding route tests stay mocked in test_onboarding_and_worker.py.
"""

import os
from collections.abc import Generator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app.core.errors import AppError
from app.core.settings import get_settings
from app.main import create_app

AUTH_USER_ID = "00000000-0000-0000-0000-000000000123"


DEFAULT_ASYNC_DB = "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres"


def _sync_engine() -> Engine:
    raw = os.environ.get("DATABASE_URL", DEFAULT_ASYNC_DB)
    if "+asyncpg" not in raw:
        pytest.skip("DATABASE_URL must use asyncpg driver for this test module")
    sync_url = raw.replace("+asyncpg", "", 1)
    try:
        eng = create_engine(sync_url, pool_pre_ping=True)
        with eng.connect() as conn:
            conn.execute(text("select 1"))
        return eng
    except OperationalError as exc:
        pytest.skip(f"Postgres not reachable for onboarding integration test: {exc}")


@pytest.fixture
def pg_engine() -> Engine:
    return _sync_engine()


def _ensure_auth_user(engine: Engine, user_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                insert into auth.users (id, instance_id, aud, role, created_at, updated_at)
                values (
                  cast(:uid as uuid),
                  '00000000-0000-0000-0000-000000000000'::uuid,
                  'authenticated',
                  'authenticated',
                  now(),
                  now()
                )
                on conflict (id) do nothing
                """
            ),
            {"uid": user_id},
        )


def _delete_agent_cascade(engine: Engine, agent_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("delete from public.agents where id = cast(:id as uuid)"), {"id": agent_id})


class _DummyVerifier:
    def verify_token(self, token: str) -> dict[str, str]:
        if token == "good-token":
            return {"sub": AUTH_USER_ID}
        raise AppError(code="auth.unauthorized", message="bad token", status_code=401)


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer good-token"}


@pytest.fixture
def integration_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """App where dev auth bypass is off so Bearer JWT sub is honored (matches DB assertions)."""
    monkeypatch.setenv("DEV_AUTH_BYPASS_ENABLED", "false")
    get_settings.cache_clear()

    async def _noop_warmup(_: object) -> None:
        return None

    monkeypatch.setattr("app.core.security.TokenVerifier.warmup", _noop_warmup)
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_onboarding_start_persists_agent_session_and_checklist(
    integration_client: TestClient, pg_engine: Engine
) -> None:
    """POST /onboarding/start creates agents row, onboarding_sessions, and seeded checklist."""
    _ensure_auth_user(pg_engine, AUTH_USER_ID)

    slug = f"it-{uuid4().hex[:10]}"
    response = integration_client.post(
        "/api/v1/onboarding/start",
        headers=_auth_header(),
        json={"name": "Integration Sales Agent", "slug": slug},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["current_step"] == 1
    agent_id = body["agent_id"]
    UUID(agent_id)

    with pg_engine.connect() as conn:
        agent = conn.execute(
            text(
                """
                select id::text, user_id::text, name, slug, status
                from public.agents
                where id = cast(:id as uuid)
                """
            ),
            {"id": agent_id},
        ).mappings().first()
        assert agent is not None
        assert agent["user_id"] == AUTH_USER_ID
        assert agent["name"] == "Integration Sales Agent"
        assert agent["slug"] == slug
        assert agent["status"] == "active"

        rel = conn.execute(
            text(
                """
                select 1 from public.agent_reliability_settings
                where agent_id = cast(:id as uuid) and user_id = cast(:uid as uuid)
                """
            ),
            {"id": agent_id, "uid": AUTH_USER_ID},
        ).scalar()
        assert rel == 1

        session = conn.execute(
            text(
                """
                select current_step, status::text, progress_pct
                from public.onboarding_sessions
                where agent_id = cast(:id as uuid) and user_id = cast(:uid as uuid)
                """
            ),
            {"id": agent_id, "uid": AUTH_USER_ID},
        ).mappings().first()
        assert session is not None
        assert int(session["current_step"]) == 1
        assert session["status"] == "in_progress"
        assert int(session["progress_pct"]) == 0

        n_check = conn.execute(
            text(
                """
                select count(*)::int as c
                from public.onboarding_checklist_items
                where agent_id = cast(:id as uuid) and user_id = cast(:uid as uuid)
                """
            ),
            {"id": agent_id, "uid": AUTH_USER_ID},
        ).scalar()
        assert n_check == 6

        agent_done = conn.execute(
            text(
                """
                select status::text, evidence->>'name' as ev_name
                from public.onboarding_checklist_items
                where agent_id = cast(:id as uuid) and user_id = cast(:uid as uuid) and item_key = 'agent_created'
                """
            ),
            {"id": agent_id, "uid": AUTH_USER_ID},
        ).mappings().first()
        assert agent_done is not None
        assert agent_done["status"] == "done"
        assert agent_done["ev_name"] == "Integration Sales Agent"

        website_row = conn.execute(
            text(
                """
                select status::text
                from public.onboarding_checklist_items
                where agent_id = cast(:id as uuid) and user_id = cast(:uid as uuid) and item_key = 'website_connected'
                """
            ),
            {"id": agent_id, "uid": AUTH_USER_ID},
        ).mappings().first()
        assert website_row["status"] == "todo"

    _delete_agent_cascade(pg_engine, agent_id)
