from fastapi import APIRouter, Depends

from app.api.deps import AuthContext, get_current_user
from app.core.settings import get_settings

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/version")
async def version() -> dict[str, str]:
    settings = get_settings()
    return {"name": settings.app_name, "version": settings.app_version, "env": settings.app_env}


@router.get("/me")
async def me(user: AuthContext = Depends(get_current_user)) -> dict[str, str]:
    return {"user_id": str(user.user_id)}

