from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_db, require_admin
from app.domains.admin.overview_service import get_admin_overview
from app.domains.admin.schemas import AdminOverview


router = APIRouter()


@router.get("/overview", response_model=AdminOverview)
async def admin_overview_route(
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminOverview:
    """Single-call aggregator for `/admin` home page (KPIs + recent feeds + workers + cost summary)."""
    return await get_admin_overview(db)
