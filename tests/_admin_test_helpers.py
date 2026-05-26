"""Shared admin-test helpers: a `_DummyVerifier` that recognises three tokens
(`admin-token`, `non-admin-token`, anything else -> AuthError) plus a TestClient
fixture that wires it through `app.api.deps.get_token_verifier`.

Each Phase 4 test file imports `admin_client` from here. Keeping it in one place
prevents drift in the auth-bypass scaffolding across 7 tests.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AuthError
from app.core.settings import get_settings
from app.main import create_app


ADMIN_EMAIL = "alice@chatrely.com"
ADMIN_USER_ID = "00000000-0000-0000-0000-000000000aaa"
NON_ADMIN_EMAIL = "bob@example.com"
NON_ADMIN_USER_ID = "00000000-0000-0000-0000-000000000bbb"


class _DummyVerifier:
    def verify_token(self, token: str) -> dict[str, str]:
        if token == "admin-token":
            return {"sub": ADMIN_USER_ID, "email": ADMIN_EMAIL}
        if token == "non-admin-token":
            return {"sub": NON_ADMIN_USER_ID, "email": NON_ADMIN_EMAIL}
        raise AuthError("bad token")


async def _noop_warmup(_self: object) -> None:
    return None


@pytest.fixture
def admin_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("DEV_AUTH_BYPASS_ENABLED", "false")
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN_EMAIL)
    get_settings.cache_clear()
    monkeypatch.setattr("app.core.security.TokenVerifier.warmup", _noop_warmup)
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())
    app = create_app()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def admin_auth() -> dict[str, str]:
    return {"Authorization": "Bearer admin-token"}


def non_admin_auth() -> dict[str, str]:
    return {"Authorization": "Bearer non-admin-token"}
