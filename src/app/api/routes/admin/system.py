from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_db, require_admin
from app.domains.admin.schemas import AdminSystemHealth
from app.domains.admin.system_service import get_admin_system_health


router = APIRouter()


@router.get("/system/health", response_model=AdminSystemHealth)
async def admin_system_health_route(
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminSystemHealth:
    """App version + DB latency probe + worker heartbeats + pricing-status (which models priced)."""
    return await get_admin_system_health(db)
