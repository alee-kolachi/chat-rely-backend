import pytest
from fastapi.testclient import TestClient

from app.core.errors import AuthError
from app.core.settings import Settings, get_settings
from app.main import create_app


ADMIN_EMAIL = "alice@chatrely.com"
ADMIN_USER_ID = "00000000-0000-0000-0000-000000000aaa"
NON_ADMIN_EMAIL = "bob@example.com"
NON_ADMIN_USER_ID = "00000000-0000-0000-0000-000000000bbb"


class _DummyVerifier:
    """Maps tokens to JWT-style claim dicts including the email used for admin gating."""

    def verify_token(self, token: str) -> dict[str, str]:
        if token == "admin-token":
            return {"sub": ADMIN_USER_ID, "email": ADMIN_EMAIL}
        if token == "admin-token-uppercase":
            # Same admin user but with the email reported in a different case to check
            # case-insensitive matching against ADMIN_EMAILS.
            return {"sub": ADMIN_USER_ID, "email": ADMIN_EMAIL.upper()}
        if token == "non-admin-token":
            return {"sub": NON_ADMIN_USER_ID, "email": NON_ADMIN_EMAIL}
        if token == "no-email-token":
            return {"sub": NON_ADMIN_USER_ID}
        raise AuthError("bad token")


async def _noop_warmup(_self: object) -> None:
    return None


@pytest.fixture
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Test client with ADMIN_EMAILS set and dev auth bypass off."""
    monkeypatch.setenv("DEV_AUTH_BYPASS_ENABLED", "false")
    monkeypatch.setenv("ADMIN_EMAILS", f"  {ADMIN_EMAIL.upper()}  , other@chatrely.com  ")
    get_settings.cache_clear()
    monkeypatch.setattr("app.core.security.TokenVerifier.warmup", _noop_warmup)
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())
    app = create_app()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def test_admin_emails_parsed_case_insensitive_with_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_EMAILS", "  Alice@Chatrely.com  ,  bob@example.com  ,  ")
    get_settings.cache_clear()
    s = get_settings()
    assert s.admin_emails == ["alice@chatrely.com", "bob@example.com"]
    assert s.is_admin_email("ALICE@chatrely.com") is True
    assert s.is_admin_email("bob@example.com") is True
    assert s.is_admin_email("eve@example.com") is False
    assert s.is_admin_email(None) is False
    assert s.is_admin_email("") is False
    get_settings.cache_clear()


def test_admin_emails_empty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert s.admin_emails == []
    assert s.is_admin_email("anyone@example.com") is False
    get_settings.cache_clear()


def test_admin_emails_parses_list_input() -> None:
    s = Settings(
        database_url="postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres",
        supabase_jwks_url="https://example.com/.well-known/jwks.json",
        supabase_issuer="https://example.com/auth/v1",
        admin_emails=["  ALICE@chatrely.com  ", "bob@example.com"],
    )
    assert s.admin_emails == ["alice@chatrely.com", "bob@example.com"]


def test_admin_me_admin_token_returns_200(admin_client: TestClient) -> None:
    response = admin_client.get(
        "/api/v1/admin/me",
        headers={"Authorization": "Bearer admin-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"email": ADMIN_EMAIL, "is_admin": True}


def test_admin_me_admin_token_email_case_insensitive(admin_client: TestClient) -> None:
    response = admin_client.get(
        "/api/v1/admin/me",
        headers={"Authorization": "Bearer admin-token-uppercase"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_admin"] is True
    # Echoes the JWT email verbatim — the matching is what's case-insensitive.
    assert body["email"] == ADMIN_EMAIL.upper()


def test_admin_me_non_admin_token_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get(
        "/api/v1/admin/me",
        headers={"Authorization": "Bearer non-admin-token"},
    )
    assert response.status_code == 404


def test_admin_me_token_without_email_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get(
        "/api/v1/admin/me",
        headers={"Authorization": "Bearer no-email-token"},
    )
    assert response.status_code == 404


def test_admin_me_missing_bearer_returns_401(admin_client: TestClient) -> None:
    response = admin_client.get("/api/v1/admin/me")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "auth.unauthorized"


def test_admin_me_invalid_bearer_returns_401(admin_client: TestClient) -> None:
    response = admin_client.get(
        "/api/v1/admin/me",
        headers={"Authorization": "Bearer garbage"},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "auth.unauthorized"


def test_admin_me_returns_404_when_allowlist_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_AUTH_BYPASS_ENABLED", "false")
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr("app.core.security.TokenVerifier.warmup", _noop_warmup)
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())
    app = create_app()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/admin/me",
            headers={"Authorization": "Bearer admin-token"},
        )
    assert response.status_code == 404
    get_settings.cache_clear()
