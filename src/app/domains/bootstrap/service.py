from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.settings import get_settings
from app.domains.bootstrap.schemas import (
    BootstrapResponse,
    MeContextResponse,
    PlanDTO,
    ProfileDTO,
    SubscriptionDTO,
    UsageSnapshotDTO,
)
log = structlog.get_logger(__name__)


async def user_dashboard_onboarding_completed(db: AsyncSession, user_id: UUID) -> bool:
    """
    Dashboard is allowed when the user finished the guided onboarding flow, or when they have
    active agent(s) but no onboarding_sessions rows (accounts created before onboarding existed).
    """
    row = (
        await db.execute(
            text(
                """
                select
                  exists (
                    select 1
                    from public.onboarding_sessions s
                    where s.user_id = cast(:user_id as uuid)
                      and s.status = 'completed'
                  ) as has_completed,
                  exists (
                    select 1
                    from public.agents a
                    where a.user_id = cast(:user_id as uuid)
                      and a.archived_at is null
                  ) as has_active_agent,
                  exists (
                    select 1
                    from public.onboarding_sessions s2
                    where s2.user_id = cast(:user_id as uuid)
                  ) as has_any_session
                """
            ),
            {"user_id": str(user_id)},
        )
    ).mappings().one()
    has_completed = bool(row["has_completed"])
    has_active_agent = bool(row["has_active_agent"])
    has_any_session = bool(row["has_any_session"])
    return has_completed or (has_active_agent and not has_any_session)


def _month_period(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month_seed = start.replace(day=28) + timedelta(days=4)
    end = next_month_seed.replace(day=1) - timedelta(microseconds=1)
    return start, end


async def _ensure_local_dev_auth_user_stub(db: AsyncSession, user_id: UUID) -> None:
    """When dev auth bypass is on, JWT / fallback sub may not exist in auth.users yet."""
    settings = get_settings()
    if not (settings.is_development and settings.dev_auth_bypass_enabled):
        return
    await db.execute(
        text(
            """
            insert into auth.users (id, instance_id, aud, role, created_at, updated_at)
            values (
              :user_id,
              '00000000-0000-0000-0000-000000000000'::uuid,
              'authenticated',
              'authenticated',
              now(),
              now()
            )
            on conflict (id) do nothing
            """
        ),
        {"user_id": str(user_id)},
    )


async def _fetch_profile(db: AsyncSession, user_id: UUID) -> ProfileDTO | None:
    result = await db.execute(
        text(
            """
            select id, full_name, avatar_url, timezone, email_notifications_enabled,
                   coalesce(notification_preferences, '{}'::jsonb) as notification_preferences,
                   created_at, updated_at
            from public.profiles
            where id = :user_id
            """
        ),
        {"user_id": str(user_id)},
    )
    row = result.mappings().first()
    return ProfileDTO.model_validate(row) if row else None


async def ensure_user_profile(db: AsyncSession, user_id: UUID) -> ProfileDTO:
    """Create public.profiles when missing (e.g. before notifications or first API use)."""
    return await _ensure_profile(db, user_id)


async def _ensure_profile(db: AsyncSession, user_id: UUID) -> ProfileDTO:
    profile = await _fetch_profile(db, user_id)
    if profile:
        return profile

    await _ensure_local_dev_auth_user_stub(db, user_id)

    result = await db.execute(
        text(
            """
            insert into public.profiles (id)
            values (:user_id)
            returning id, full_name, avatar_url, timezone, email_notifications_enabled,
              coalesce(notification_preferences, '{}'::jsonb) as notification_preferences,
              created_at, updated_at
            """
        ),
        {"user_id": str(user_id)},
    )
    return ProfileDTO.model_validate(result.mappings().one())


async def _fetch_active_subscription_and_plan(
    db: AsyncSession, user_id: UUID
) -> tuple[SubscriptionDTO, PlanDTO] | None:
    result = await db.execute(
        text(
            """
            select
              s.id as subscription_id,
              s.user_id,
              s.plan_id,
              s.status,
              s.current_period_start,
              s.current_period_end,
              s.cancel_at_period_end,
              s.provider_customer_id,
              s.provider_subscription_id,
              p.id as plan_id_ref,
              p.slug,
              p.name,
              p.monthly_price_cents,
              p.included_conversations,
              p.max_agents,
              p.overage_conversation_cents,
              p.features
            from public.subscriptions s
            join public.plans p on p.id = s.plan_id
            where s.user_id = :user_id
              and s.status in ('trialing', 'active', 'past_due')
            order by s.current_period_end desc
            limit 1
            """
        ),
        {"user_id": str(user_id)},
    )
    row = result.mappings().first()
    if not row:
        return None

    subscription = SubscriptionDTO.model_validate(
        {
            "id": row["subscription_id"],
            "user_id": row["user_id"],
            "plan_id": row["plan_id"],
            "status": row["status"],
            "current_period_start": row["current_period_start"],
            "current_period_end": row["current_period_end"],
            "cancel_at_period_end": row["cancel_at_period_end"],
            "provider_customer_id": row.get("provider_customer_id"),
            "provider_subscription_id": row.get("provider_subscription_id"),
        }
    )
    plan = PlanDTO.model_validate(
        {
            "id": row["plan_id_ref"],
            "slug": row["slug"],
            "name": row["name"],
            "monthly_price_cents": int(row.get("monthly_price_cents") or 0),
            "included_conversations": row["included_conversations"],
            "max_agents": row["max_agents"],
            "overage_conversation_cents": row["overage_conversation_cents"],
            "features": row["features"] or {},
        }
    )
    return subscription, plan


async def _default_plan_id_for_new_subscription(db: AsyncSession) -> UUID:
    """Prefer free tier for new workspaces; fall back to hobby if migrations are partial."""
    for slug in ("free", "hobby"):
        res = await db.execute(
            text("select id from public.plans where slug = :slug and is_active = true limit 1"),
            {"slug": slug},
        )
        row = res.mappings().first()
        if row:
            return UUID(str(row["id"]))
    raise AppError(
        code="plan.not_found",
        message="No active free or hobby plan in database. Apply Supabase migrations / seed.",
        status_code=500,
    )


async def _ensure_default_subscription(db: AsyncSession, user_id: UUID) -> tuple[SubscriptionDTO, PlanDTO]:
    existing = await _fetch_active_subscription_and_plan(db, user_id)
    if existing:
        return existing

    plan_id = await _default_plan_id_for_new_subscription(db)
    period_start, period_end = _month_period(datetime.now(tz=UTC))
    await db.execute(
        text(
            """
            insert into public.subscriptions (
              user_id, plan_id, status, current_period_start, current_period_end, cancel_at_period_end
            ) values (
              :user_id, :plan_id, 'active', :period_start, :period_end, false
            )
            """
        ),
        {
            "user_id": str(user_id),
            "plan_id": str(plan_id),
            "period_start": period_start,
            "period_end": period_end,
        },
    )
    refreshed = await _fetch_active_subscription_and_plan(db, user_id)
    if refreshed is None:
        raise AppError(code="subscription.bootstrap_failed", message="Failed to create default subscription", status_code=500)
    return refreshed


async def _refresh_usage_snapshot_for_subscription(
    db: AsyncSession, user_id: UUID, subscription: SubscriptionDTO
) -> None:
    ps = subscription.current_period_start.astimezone(UTC).date()
    pe = subscription.current_period_end.astimezone(UTC).date()
    await db.execute(
        text(
            "select public.refresh_usage_period_snapshot(cast(:uid as uuid), cast(:ps as date), cast(:pe as date))"
        ),
        {"uid": str(user_id), "ps": ps, "pe": pe},
    )


async def _fetch_context_profile_subscription_plan(
    db: AsyncSession, user_id: UUID
) -> tuple[ProfileDTO, SubscriptionDTO, PlanDTO] | None:
    result = await db.execute(
        text(
            """
            with active_subscription as (
              select
                s.id as subscription_id,
                s.user_id,
                s.plan_id,
                s.status,
                s.current_period_start,
                s.current_period_end,
                s.cancel_at_period_end,
                s.provider_customer_id,
                s.provider_subscription_id
              from public.subscriptions s
              where s.user_id = :user_id
                and s.status in ('trialing', 'active', 'past_due')
              order by s.current_period_end desc
              limit 1
            )
            select
              p.id as profile_id,
              p.full_name,
              p.avatar_url,
              p.timezone,
              p.email_notifications_enabled,
              coalesce(p.notification_preferences, '{}'::jsonb) as notification_preferences,
              p.created_at as profile_created_at,
              p.updated_at as profile_updated_at,
              s.subscription_id,
              s.user_id as subscription_user_id,
              s.plan_id as subscription_plan_id,
              s.status as subscription_status,
              s.current_period_start,
              s.current_period_end,
              s.cancel_at_period_end,
              s.provider_customer_id,
              s.provider_subscription_id,
              pl.id as plan_id_ref,
              pl.slug as plan_slug,
              pl.name as plan_name,
              pl.monthly_price_cents as plan_monthly_price_cents,
              pl.included_conversations as plan_included_conversations,
              pl.max_agents as plan_max_agents,
              pl.overage_conversation_cents as plan_overage_conversation_cents,
              pl.features as plan_features
            from public.profiles p
            left join active_subscription s on true
            left join public.plans pl on pl.id = s.plan_id
            where p.id = :user_id
            """
        ),
        {"user_id": str(user_id)},
    )
    row = result.mappings().first()
    if not row:
        return None
    if not row["subscription_id"] or not row["plan_id_ref"]:
        return None

    np = row.get("notification_preferences")
    if not isinstance(np, dict):
        np = {}
    profile = ProfileDTO.model_validate(
        {
            "id": row["profile_id"],
            "full_name": row["full_name"],
            "avatar_url": row["avatar_url"],
            "timezone": row["timezone"],
            "email_notifications_enabled": row["email_notifications_enabled"],
            "notification_preferences": np,
            "created_at": row["profile_created_at"],
            "updated_at": row["profile_updated_at"],
        }
    )
    subscription = SubscriptionDTO.model_validate(
        {
            "id": row["subscription_id"],
            "user_id": row["subscription_user_id"],
            "plan_id": row["subscription_plan_id"],
            "status": row["subscription_status"],
            "current_period_start": row["current_period_start"],
            "current_period_end": row["current_period_end"],
            "cancel_at_period_end": row["cancel_at_period_end"],
            "provider_customer_id": row["provider_customer_id"],
            "provider_subscription_id": row["provider_subscription_id"],
        }
    )
    plan = PlanDTO.model_validate(
        {
            "id": row["plan_id_ref"],
            "slug": row["plan_slug"],
            "name": row["plan_name"],
            "monthly_price_cents": int(row.get("plan_monthly_price_cents") or 0),
            "included_conversations": row["plan_included_conversations"],
            "max_agents": row["plan_max_agents"],
            "overage_conversation_cents": row["plan_overage_conversation_cents"],
            "features": row["plan_features"] or {},
        }
    )
    return profile, subscription, plan


async def bootstrap_me(db: AsyncSession, user_id: UUID) -> BootstrapResponse:
    profile = await _ensure_profile(db, user_id)
    subscription, plan = await _ensure_default_subscription(db, user_id)
    onboarding_completed = await user_dashboard_onboarding_completed(db, user_id)
    await db.commit()
    return BootstrapResponse(
        profile=profile, subscription=subscription, plan=plan, onboarding_completed=onboarding_completed
    )


async def fetch_me_context(db: AsyncSession, user_id: UUID) -> MeContextResponse:
    context = await _fetch_context_profile_subscription_plan(db, user_id)
    if context is None:
        profile = await _ensure_profile(db, user_id)
        subscription, plan = await _ensure_default_subscription(db, user_id)
        await db.commit()
    else:
        profile, subscription, plan = context
    ps = subscription.current_period_start.astimezone(UTC).date()
    pe = subscription.current_period_end.astimezone(UTC).date()
    usage_result = await db.execute(
        text(
            """
            select
              period_start,
              period_end,
              included_conversations,
              conversations_used,
              overage_conversations,
              estimated_overage_cents,
              throttle_tier,
              coalesce(included_premium_turns, 0) as included_premium_turns,
              coalesce(premium_turns_used, 0) as premium_turns_used
            from public.usage_period_snapshots
            where user_id = :user_id
              and period_start = :ps
              and period_end = :pe
            """
        ),
        {"user_id": str(user_id), "ps": ps, "pe": pe},
    )
    usage_row = usage_result.mappings().first()
    await db.commit()

    usage_snapshot: UsageSnapshotDTO | None = None
    if usage_row:
        usage_snapshot = UsageSnapshotDTO.model_validate(dict(usage_row))
    onboarding_completed = await user_dashboard_onboarding_completed(db, user_id)
    return MeContextResponse(
        profile=profile,
        subscription=subscription,
        plan=plan,
        usage_snapshot=usage_snapshot,
        onboarding_completed=onboarding_completed,
    )


async def refresh_usage_snapshot_for_user(user_id: UUID) -> None:
    from app.db.session import get_session_factory

    async with get_session_factory()() as db:
        try:
            context = await _fetch_context_profile_subscription_plan(db, user_id)
            if context is None:
                return
            _, subscription, _ = context
            await _refresh_usage_snapshot_for_subscription(db, user_id, subscription)
            await db.commit()
            ps = subscription.current_period_start.astimezone(UTC).date()
            pe = subscription.current_period_end.astimezone(UTC).date()
            from app.domains.notifications.service import maybe_emit_usage_warning_notification

            await maybe_emit_usage_warning_notification(
                db, user_id=user_id, period_start=ps, period_end=pe
            )
        except Exception:
            log.warning("usage.snapshot_refresh_failed", user_id=str(user_id), exc_info=True)

