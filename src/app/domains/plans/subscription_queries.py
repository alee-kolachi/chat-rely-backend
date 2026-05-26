"""Shared read-only subscription ↔ plan lookups (no side effects)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.plans.plan_limits import PlanModelPolicy, plan_model_policy_from_features


async def fetch_active_plan_slug(db: AsyncSession, user_id: UUID) -> str | None:
    """Latest active subscription's plan slug for the user, if any."""
    row = (
        await db.execute(
            text(
                """
                select p.slug::text as slug
                from public.subscriptions s
                join public.plans p on p.id = s.plan_id
                where s.user_id = cast(:uid as uuid)
                  and s.status in ('trialing', 'active', 'past_due')
                order by s.current_period_end desc
                limit 1
                """
            ),
            {"uid": str(user_id)},
        )
    ).mappings().first()
    if not row or not row.get("slug"):
        return None
    return str(row["slug"])


async def fetch_active_plan_model_policy(db: AsyncSession, user_id: UUID) -> PlanModelPolicy | None:
    """Active subscription plan slug, features, and throttle policy for model routing."""
    row = (
        await db.execute(
            text(
                """
                select
                  p.slug::text as slug,
                  coalesce(p.features, '{}'::jsonb) as features,
                  coalesce(p.throttle_policy, '{}'::jsonb) as throttle_policy
                from public.subscriptions s
                join public.plans p on p.id = s.plan_id
                where s.user_id = cast(:uid as uuid)
                  and s.status in ('trialing', 'active', 'past_due')
                order by s.current_period_end desc
                limit 1
                """
            ),
            {"uid": str(user_id)},
        )
    ).mappings().first()
    if not row or not row.get("slug"):
        return None
    return plan_model_policy_from_features(
        str(row["slug"]),
        row.get("features") or {},
        throttle_policy=row.get("throttle_policy") or {},
    )
