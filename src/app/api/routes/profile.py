from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user, get_db
from app.domains.profile.schemas import MeProfileResponse, UpdateProfileRequest
from app.domains.profile.service import get_me_profile, update_me_profile

router = APIRouter(tags=["profile"])


@router.get("/me/profile", response_model=MeProfileResponse)
async def get_my_profile(
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MeProfileResponse:
    return await get_me_profile(db, user.user_id)


@router.patch("/me/profile", response_model=MeProfileResponse)
async def patch_my_profile(
    body: UpdateProfileRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MeProfileResponse:
    return await update_me_profile(db, user.user_id, body)
