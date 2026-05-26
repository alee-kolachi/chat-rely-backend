import pytest
from fastapi.testclient import TestClient

from app.core.errors import AuthError
from app.core.settings import get_settings
from app.main import create_app


class _DummyVerifier:
    def verify_token(self, token: str) -> dict[str, str]:
        if token == "good-token":
            return {"sub": "00000000-0000-0000-0000-000000000123"}
        raise AuthError("bad token")


async def _noop_warmup(_self: object) -> None:
    return None


@pytest.fixture
def strict_auth_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App with dev auth bypass off so missing/invalid tokens follow real auth rules."""
    monkeypatch.setenv("DEV_AUTH_BYPASS_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("app.core.security.TokenVerifier.warmup", _noop_warmup)
    app = create_app()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def test_missing_bearer_returns_standard_error(strict_auth_client: TestClient) -> None:
    response = strict_auth_client.get("/api/v1/system/me")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "auth.unauthorized"
    assert body["error"]["request_id"] is not None


def test_valid_bearer_returns_user(strict_auth_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())
    response = strict_auth_client.get("/api/v1/system/me", headers={"Authorization": "Bearer good-token"})
    assert response.status_code == 200
    assert response.json() == {"user_id": "00000000-0000-0000-0000-000000000123"}


def test_not_found_uses_standard_error(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "http.error"


def test_invalid_bearer_uses_auth_error(strict_auth_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())
    response = strict_auth_client.get("/api/v1/system/me", headers={"Authorization": "Bearer bad-token"})
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "auth.unauthorized"
