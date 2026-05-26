from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.plans.schemas import PublicPlanDTO


async def list_public_pricing_plans(db: AsyncSession) -> list[PublicPlanDTO]:
    result = await db.execute(
        text(
            """
            select
              slug,
              name,
              monthly_price_cents,
              included_conversations,
              overage_conversation_cents,
              max_agents,
              coalesce(features, '{}'::jsonb) as features,
              coalesce(throttle_policy, '{}'::jsonb) as throttle_policy,
              sort_order
            from public.plans
            where is_active = true
              and public_on_pricing_page = true
            order by sort_order asc, name asc
            """
        )
    )
    rows = result.mappings().all()
    return [PublicPlanDTO.model_validate(dict(r)) for r in rows]
