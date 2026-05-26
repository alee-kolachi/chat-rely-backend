from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.db.session import check_db_ready

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(_: object = Depends(get_db)) -> dict[str, str]:
    is_ready = await check_db_ready()
    return {"status": "ok" if is_ready else "degraded"}

