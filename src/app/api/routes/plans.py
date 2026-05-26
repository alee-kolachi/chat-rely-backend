from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.domains.plans.schemas import PublicPlanDTO
from app.domains.plans.service import list_public_pricing_plans

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("/public", response_model=list[PublicPlanDTO])
async def list_public_plans_route(db: AsyncSession = Depends(get_db)) -> list[PublicPlanDTO]:
    """Active plans shown on the marketing site (typically four of five tiers)."""
    return await list_public_pricing_plans(db)
