from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user, get_db
from app.domains.bootstrap.schemas import BootstrapResponse, MeContextResponse, OnboardingGateResponse
from app.domains.bootstrap.service import (
    bootstrap_me,
    fetch_me_context,
    refresh_usage_snapshot_for_user,
    user_dashboard_onboarding_completed,
)

router = APIRouter(tags=["bootstrap"])


@router.post("/bootstrap/me", response_model=BootstrapResponse)
async def bootstrap_me_route(
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BootstrapResponse:
    return await bootstrap_me(db, user.user_id)


@router.get("/me/onboarding-gate", response_model=OnboardingGateResponse)
async def me_onboarding_gate_route(
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OnboardingGateResponse:
    completed = await user_dashboard_onboarding_completed(db, user.user_id)
    return OnboardingGateResponse(onboarding_completed=completed)


@router.get("/me/context", response_model=MeContextResponse)
async def me_context_route(
    background_tasks: BackgroundTasks,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MeContextResponse:
    response = await fetch_me_context(db, user.user_id)
    background_tasks.add_task(refresh_usage_snapshot_for_user, user.user_id)
    return response

