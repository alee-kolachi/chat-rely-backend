"""Admin plans browser.

Returns *every* plan, including `is_active=false`. The user-facing `/api/v1/plans/public`
filters those out so the marketing page stays clean; admins want the full picture for
auditing legacy customers still on inactive plans.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.admin.schemas import AdminPlanListResponse, AdminPlanRow


async def list_admin_plans(db: AsyncSession) -> AdminPlanListResponse:
    rows = (
        await db.execute(
            text(
                """
                select
                  p.id, p.slug, p.name,
                  p.monthly_price_cents, p.included_conversations,
                  p.overage_conversation_cents, p.max_agents,
                  p.features, p.throttle_policy, p.is_active,
                  p.public_on_pricing_page, p.sort_order,
                  coalesce(sc.cnt, 0)::int as subscriptions_count,
                  p.created_at
                from public.plans p
                left join lateral (
                  select count(*)::int as cnt
                  from public.subscriptions s
                  where s.plan_id = p.id
                ) sc on true
                order by p.is_active desc, p.monthly_price_cents asc, p.created_at asc
                """
            )
        )
    ).mappings().all()

    items = [
        AdminPlanRow.model_validate(
            {
                **dict(r),
                "features": r["features"] or {},
                "throttle_policy": r["throttle_policy"] or {},
            }
        )
        for r in rows
    ]
    return AdminPlanListResponse(items=items)
