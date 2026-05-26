from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_db, require_admin
from app.domains.admin.plans_service import list_admin_plans
from app.domains.admin.schemas import AdminPlanListResponse


router = APIRouter()


@router.get("/plans", response_model=AdminPlanListResponse)
async def list_admin_plans_route(
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminPlanListResponse:
    """All plans, including `is_active=false`. Marketing endpoint hides inactive ones."""
    return await list_admin_plans(db)
