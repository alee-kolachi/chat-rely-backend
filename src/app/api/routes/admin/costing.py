from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_db, require_admin
from app.domains.admin.costing_service import (
    get_conversation_cost,
    get_platform_costing_overview,
    get_top_spenders_leaderboard,
    get_user_costing,
    get_worst_margin_leaderboard,
)
from app.domains.admin.schemas import (
    AdminConversationCost,
    AdminCostingLeaderboard,
    AdminPlatformCosting,
    AdminUserCosting,
)


router = APIRouter()


LeaderboardMetric = Literal["worst_margin", "top_spend"]


@router.get("/costing/overview", response_model=AdminPlatformCosting)
async def admin_costing_overview_route(
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminPlatformCosting:
    """Platform-wide LLM/embedding spend, revenue, gross margin (MTD vs prior month).

    Cached in-process for `PLATFORM_CACHE_TTL_SECONDS` (60s) to keep dashboard renders cheap.
    """
    return await get_platform_costing_overview(db)


@router.get("/costing/leaderboard", response_model=AdminCostingLeaderboard)
async def admin_costing_leaderboard_route(
    metric: LeaderboardMetric = Query(default="worst_margin"),
    limit: int = Query(default=20, ge=1, le=100),
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminCostingLeaderboard:
    if metric == "worst_margin":
        return await get_worst_margin_leaderboard(db, limit=limit)
    return await get_top_spenders_leaderboard(db, limit=limit)


@router.get("/conversations/{conversation_id}/cost", response_model=AdminConversationCost)
async def admin_conversation_cost_route(
    conversation_id: UUID,
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminConversationCost:
    return await get_conversation_cost(db, conversation_id)


@router.get("/users/{user_id}/costing", response_model=AdminUserCosting)
async def admin_user_costing_route(
    user_id: UUID,
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserCosting:
    return await get_user_costing(db, user_id)
