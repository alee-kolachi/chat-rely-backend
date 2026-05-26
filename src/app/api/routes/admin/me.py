from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import AuthContext, require_admin

router = APIRouter()


@router.get("/me")
async def admin_me(auth: AuthContext = Depends(require_admin)) -> dict[str, Any]:
    """Probe endpoint used by the frontend `(admin)` layout to gate UI access.

    Returns 200 + admin identity for users in `ADMIN_EMAILS`. Non-admins are blocked
    by `require_admin` with HTTP 404 so the route's existence stays hidden.
    """
    return {"email": auth.claims.get("email"), "is_admin": True}
