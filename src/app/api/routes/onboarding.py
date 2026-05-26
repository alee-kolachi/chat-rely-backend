from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user, get_db
from app.domains.onboarding.schemas import (
    OnboardingFinishRequest,
    OnboardingPreferencesRequest,
    OnboardingStartRequest,
    OnboardingStartResponse,
    OnboardingStatusResponse,
    OnboardingWebsiteRequest,
    OnboardingWebsiteResponse,
)
from app.domains.onboarding.service import (
    finish_onboarding,
    get_onboarding_status,
    save_preferences,
    start_onboarding,
    submit_website,
)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.post("/start", response_model=OnboardingStartResponse)
async def onboarding_start_route(
    payload: OnboardingStartRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OnboardingStartResponse:
    return await start_onboarding(db, user.user_id, payload)


@router.post("/website", response_model=OnboardingWebsiteResponse)
async def onboarding_website_route(
    payload: OnboardingWebsiteRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OnboardingWebsiteResponse:
    return await submit_website(db, user.user_id, payload)


@router.get("/status", response_model=OnboardingStatusResponse)
async def onboarding_status_route(
    agent_id: UUID = Query(...),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OnboardingStatusResponse:
    return await get_onboarding_status(db, user.user_id, agent_id)


@router.patch("/preferences")
async def onboarding_preferences_route(
    payload: OnboardingPreferencesRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    await save_preferences(db, user.user_id, payload)
    return {"status": "ok"}


@router.post("/finish")
async def onboarding_finish_route(
    payload: OnboardingFinishRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    await finish_onboarding(db, user.user_id, payload)
    return {"status": "completed"}
