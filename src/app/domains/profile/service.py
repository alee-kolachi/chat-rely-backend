import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.domains.bootstrap.service import _ensure_profile
from app.domains.profile.schemas import MeProfileResponse, UpdateProfileRequest


async def _fetch_auth_email(db: AsyncSession, user_id: UUID) -> str | None:
    result = await db.execute(
        text("select email from auth.users where id = cast(:user_id as uuid)"),
        {"user_id": str(user_id)},
    )
    row = result.mappings().first()
    if not row:
        return None
    return row["email"]


async def get_me_profile(db: AsyncSession, user_id: UUID) -> MeProfileResponse:
    profile = await _ensure_profile(db, user_id)
    email = await _fetch_auth_email(db, user_id)
    return MeProfileResponse(
        id=profile.id,
        email=(email or "").strip(),
        full_name=profile.full_name,
        avatar_url=profile.avatar_url,
        timezone=profile.timezone,
        email_notifications_enabled=profile.email_notifications_enabled,
        notification_preferences=dict(profile.notification_preferences or {}),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


async def update_me_profile(
    db: AsyncSession, user_id: UUID, body: UpdateProfileRequest
) -> MeProfileResponse:
    data = body.model_dump(exclude_unset=True)
    if not data:
        return await get_me_profile(db, user_id)

    await _ensure_profile(db, user_id)

    if "full_name" in data:
        await db.execute(
            text(
                """
                update public.profiles
                set full_name = :full_name
                where id = cast(:user_id as uuid)
                """
            ),
            {"user_id": str(user_id), "full_name": data["full_name"]},
        )

    if "email" in data and data["email"] is not None:
        new_email = str(data["email"]).strip().lower()
        try:
            email_result = await db.execute(
                text(
                    """
                    update auth.users
                    set email = :email,
                        updated_at = now()
                    where id = cast(:user_id as uuid)
                    returning email
                    """
                ),
                {"user_id": str(user_id), "email": new_email},
            )
        except IntegrityError as exc:
            await db.rollback()
            raise AppError(
                code="profile.email_conflict",
                message="That email is already in use.",
                status_code=409,
            ) from exc
        if email_result.mappings().first() is None:
            await db.rollback()
            raise AppError(
                code="profile.email_update_failed",
                message="Could not update email for this account.",
                status_code=400,
            )

    if "notification_preferences" in data and data["notification_preferences"] is not None:
        patch = data["notification_preferences"]
        if not isinstance(patch, dict):
            await db.rollback()
            raise AppError(
                code="profile.invalid_notification_preferences",
                message="notification_preferences must be an object",
                status_code=400,
            )
        current = await _ensure_profile(db, user_id)
        merged = dict(current.notification_preferences or {})
        for k, v in patch.items():
            if isinstance(v, bool):
                merged[str(k)[:128]] = v

        await db.execute(
            text(
                """
                update public.profiles
                set notification_preferences = cast(:prefs as jsonb),
                    updated_at = now()
                where id = cast(:user_id as uuid)
                """
            ),
            {"user_id": str(user_id), "prefs": json.dumps(merged)},
        )

    await db.commit()
    return await get_me_profile(db, user_id)
