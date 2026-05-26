from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_state import get_token_verifier
from app.core.errors import AuthError
from app.core.settings import get_settings
from app.db.session import get_db_session

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    user_id: UUID
    claims: dict[str, Any]


def get_db(session: AsyncSession = Depends(get_db_session)) -> AsyncSession:
    return session


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthContext:
    settings = get_settings()
    if credentials is None:
        if settings.is_development and settings.dev_auth_bypass_enabled:
            # Local-only: unauthenticated tools / curl against a single shared dev user.
            fallback_claims = {"sub": settings.dev_auth_bypass_user_id, "role": "dev_bypass"}
            return AuthContext(user_id=UUID(settings.dev_auth_bypass_user_id), claims=fallback_claims)
        raise AuthError("Missing bearer token")

    claims = get_token_verifier().verify_token(credentials.credentials)
    try:
        return AuthContext(user_id=UUID(str(claims["sub"])), claims=claims)
    except (TypeError, ValueError) as exc:
        raise AuthError("Token subject is not a valid UUID") from exc


def require_admin(auth: AuthContext = Depends(get_current_user)) -> AuthContext:
    """Gate `/api/v1/admin/*` routes against the ADMIN_EMAILS allowlist.

    Returns 404 (not 403) so non-admins cannot confirm the admin namespace exists.
    """
    settings = get_settings()
    email = auth.claims.get("email")
    if not isinstance(email, str) or not settings.is_admin_email(email):
        raise HTTPException(status_code=404, detail="Not Found")
    return auth

