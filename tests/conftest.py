import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.core.settings import get_settings
from app.main import create_app


@pytest.fixture(autouse=True)
def _env() -> Generator[None, None, None]:
    os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres"
    os.environ["SUPABASE_JWKS_URL"] = "https://example.com/.well-known/jwks.json"
    os.environ["SUPABASE_ISSUER"] = "https://example.com/auth/v1"
    os.environ["SUPABASE_AUDIENCE"] = "authenticated"
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    async def _noop_warmup(self: object) -> None:
        return None

    async def _noop_db() -> None:
        return None

    monkeypatch.setattr("app.core.security.TokenVerifier.warmup", _noop_warmup)
    monkeypatch.setattr("app.main.check_db_ready", _noop_db)
    monkeypatch.setattr("app.main.warm_all_runtime_caches", _noop_db)
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

