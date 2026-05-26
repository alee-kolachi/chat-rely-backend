from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user
from app.domains.profile.schemas import MeProfileResponse, UpdateProfileRequest


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer good-token"}


def _override_user(client: TestClient) -> None:
    uid = UUID("00000000-0000-0000-0000-000000000123")
    client.app.dependency_overrides[get_current_user] = lambda: AuthContext(
        user_id=uid,
        claims={"sub": str(uid)},
    )


def _clear_override(client: TestClient) -> None:
    client.app.dependency_overrides.pop(get_current_user, None)


def test_get_me_profile(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _override_user(client)

    async def _get(_db: AsyncSession, user_id: UUID) -> MeProfileResponse:
        assert str(user_id) == "00000000-0000-0000-0000-000000000123"
        return MeProfileResponse.model_validate(
            {
                "id": "00000000-0000-0000-0000-000000000123",
                "email": "user@example.com",
                "full_name": "Ada Lovelace",
                "avatar_url": None,
                "timezone": "UTC",
                "email_notifications_enabled": True,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        )

    monkeypatch.setattr("app.api.routes.profile.get_me_profile", _get)
    try:
        response = client.get("/api/v1/me/profile", headers=_auth_header())
    finally:
        _clear_override(client)
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "user@example.com"
    assert body["full_name"] == "Ada Lovelace"


def test_patch_me_profile(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _override_user(client)

    async def _patch(_db: AsyncSession, _user_id: UUID, _body: UpdateProfileRequest) -> MeProfileResponse:
        return MeProfileResponse.model_validate(
            {
                "id": "00000000-0000-0000-0000-000000000123",
                "email": "new@example.com",
                "full_name": "Updated",
                "avatar_url": "https://example.com/a.png",
                "timezone": "UTC",
                "email_notifications_enabled": True,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
            }
        )

    monkeypatch.setattr("app.api.routes.profile.update_me_profile", _patch)
    try:
        response = client.patch(
            "/api/v1/me/profile",
            headers=_auth_header(),
            json={"full_name": "Updated", "email": "new@example.com"},
        )
    finally:
        _clear_override(client)
    assert response.status_code == 200
    assert response.json()["email"] == "new@example.com"
