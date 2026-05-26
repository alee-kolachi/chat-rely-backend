"""Single-call aggregator for the `/admin` home page.

We hit several small queries in one round-trip rather than rendering the page from a
dozen separate endpoints. Cost figures piggy-back on the cached Phase 3 platform
overview, so the home page is not the only thing paying for SUM(messages.*).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.domains.admin.costing import build_llm_cost_usd_expr
from app.domains.admin.costing_service import get_platform_costing_overview
from app.domains.admin.schemas import (
    AdminConversationListItem,
    AdminIndexingJobRow,
    AdminOverview,
    AdminOverviewKpis,
    AdminStripeEventRow,
    AdminUserListItem,
    AdminWorkerStatus,
)


# Used both for "indexing_failed_24h" KPI and the 24h cutoff on the failures feed below.
_FAILURE_WINDOW_HOURS = 24


async def get_admin_overview(
    db: AsyncSession,
    *,
    settings: Settings | None = None,
) -> AdminOverview:
    settings = settings or get_settings()
    now = datetime.now(tz=timezone.utc)

    # KPI bucket — most of these are simple counts. We deliberately don't fold them into
    # a single CTE because they hit different tables/indexes and the planner does the
    # right thing with separate queries.
    kpi_row = (
        await db.execute(
            text(
                f"""
                with sub_users as (
                  select s.user_id
                  from public.subscriptions s
                  where s.status in ('active', 'trialing')
                ),
                mrr as (
                  select coalesce(sum(p.monthly_price_cents), 0)::bigint as cents
                  from public.subscriptions s
                  join public.plans p on p.id = s.plan_id
                  where s.status in ('active', 'trialing')
                )
                select
                  (select count(*)::int from auth.users) as total_users,
                  (
                    select count(*)::int from auth.users u
                    where u.created_at >= date_trunc('day', now() at time zone 'UTC')
                  ) as signups_today,
                  (
                    select count(*)::int from auth.users u
                    where u.created_at >= now() - interval '7 days'
                  ) as signups_last_7d,
                  (select count(*)::int from sub_users) as active_subscriptions,
                  ((select cents from mrr)::float / 100.0) as mrr_usd,
                  (
                    select count(*)::int from public.conversations c
                    where c.started_at >= date_trunc('day', now() at time zone 'UTC')
                  ) as conversations_today,
                  (
                    select count(*)::int from public.conversations c
                    where c.started_at >= date_trunc('month', now() at time zone 'UTC')
                  ) as conversations_mtd,
                  (
                    select count(*)::int from public.indexing_jobs ij
                    where ij.status in ('queued', 'running')
                  ) as indexing_queue_depth,
                  (
                    select count(*)::int from public.indexing_jobs ij
                    where ij.status = 'failed'
                      and ij.created_at >= now() - interval '{_FAILURE_WINDOW_HOURS} hours'
                  ) as indexing_failed_24h
                """
            )
        )
    ).mappings().one()

    kpis = AdminOverviewKpis(
        total_users=int(kpi_row["total_users"]),
        signups_today=int(kpi_row["signups_today"]),
        signups_last_7d=int(kpi_row["signups_last_7d"]),
        active_subscriptions=int(kpi_row["active_subscriptions"]),
        mrr_usd=float(kpi_row["mrr_usd"] or 0.0),
        conversations_today=int(kpi_row["conversations_today"]),
        conversations_mtd=int(kpi_row["conversations_mtd"]),
        indexing_queue_depth=int(kpi_row["indexing_queue_depth"]),
        indexing_failed_24h=int(kpi_row["indexing_failed_24h"]),
    )

    # Recent signups — the 5 most recently created users. Mirrors AdminUserListItem
    # so the frontend can reuse the same card layout used by /admin/users.
    recent_users_rows = (
        await db.execute(
            text(
                """
                select
                  u.id, coalesce(u.email, '') as email, p.full_name, u.created_at as signed_up_at,
                  null::text as plan_slug, null::text as plan_name, null::text as subscription_status,
                  0 as agents_count, 0 as conversations_mtd,
                  null::timestamptz as last_activity_at,
                  0.0::float as revenue_mtd_usd,
                  0.0::float as llm_cost_mtd_usd,
                  0.0::float as embedding_cost_mtd_usd,
                  0.0::float as total_cost_mtd_usd,
                  0.0::float as margin_mtd_usd
                from auth.users u
                left join public.profiles p on p.id = u.id
                order by u.created_at desc
                limit 5
                """
            )
        )
    ).mappings().all()
    recent_signups = [
        AdminUserListItem.model_validate({**dict(r), "margin_pct_mtd": None})
        for r in recent_users_rows
    ]

    # Recent conversations — last 5 across the platform. Cost piggybacks on the LLM
    # expression because the Overview shows the full AdminConversationListItem shape.
    llm_expr = build_llm_cost_usd_expr(
        settings.llm_input_price_per_million_usd,
        settings.llm_output_price_per_million_usd,
        alias="m",
    )
    recent_convs_rows = (
        await db.execute(
            text(
                f"""
                select
                  c.id, c.started_at, c.last_activity_at, c.status, c.channel, c.visitor_id,
                  c.agent_id, a.name as agent_name,
                  c.user_id, coalesce(u.email, '') as user_email,
                  c.customer_message_count, c.assistant_message_count, c.tool_call_count,
                  c.total_input_tokens, c.total_output_tokens,
                  coalesce((c.metadata->>'fallback_used')::boolean, false) as fallback_used,
                  (
                    select left(m.content, 200)
                    from public.messages m
                    where m.conversation_id = c.id
                    order by m.created_at desc
                    limit 1
                  ) as latest_message_preview,
                  (
                    select sum({llm_expr})::float
                    from public.messages m
                    where m.conversation_id = c.id
                  ) as cost_usd
                from public.conversations c
                join public.agents a on a.id = c.agent_id
                join auth.users u on u.id = c.user_id
                order by c.last_activity_at desc
                limit 5
                """
            )
        )
    ).mappings().all()
    recent_conversations = [
        AdminConversationListItem.model_validate(r) for r in recent_convs_rows
    ]

    stripe_rows = (
        await db.execute(
            text(
                """
                select id, stripe_event_id, event_type, processed_at
                from public.stripe_webhook_events
                order by processed_at desc
                limit 10
                """
            )
        )
    ).mappings().all()
    recent_stripe_events = [AdminStripeEventRow.model_validate(r) for r in stripe_rows]

    # Recent indexing failures — last 10 in the trailing 24h. We don't pad this with
    # older failures because they're either already triaged or worth exploring on the
    # dedicated /admin/knowledge Indexing Jobs tab.
    failure_cutoff = now - timedelta(hours=_FAILURE_WINDOW_HOURS)
    failure_rows = (
        await db.execute(
            text(
                """
                select
                  ij.id, ij.user_id, coalesce(u.email, '') as user_email,
                  ij.agent_id, a.name as agent_name,
                  ij.knowledge_source_id, ks.title as knowledge_source_title,
                  ij.status::text as status, ij.attempt, ij.triggered_by,
                  ij.error_message, ij.started_at, ij.finished_at,
                  case
                    when ij.started_at is not null and ij.finished_at is not null
                      then (extract(epoch from (ij.finished_at - ij.started_at)) * 1000)::int
                    else null
                  end as duration_ms,
                  ij.created_at
                from public.indexing_jobs ij
                join auth.users u on u.id = ij.user_id
                join public.agents a on a.id = ij.agent_id
                join public.knowledge_sources ks on ks.id = ij.knowledge_source_id
                where ij.status = 'failed'
                  and ij.created_at >= :cutoff
                order by ij.created_at desc
                limit 10
                """
            ),
            {"cutoff": failure_cutoff},
        )
    ).mappings().all()
    recent_indexing_failures = [
        AdminIndexingJobRow.model_validate(r) for r in failure_rows
    ]

    worker_row = (
        await db.execute(
            text(
                """
                select
                  (
                    select max(finished_at)
                    from public.indexing_jobs
                    where status = 'succeeded'
                  ) as indexing_last_success_at,
                  (
                    select max(coalesce(started_at, created_at))
                    from public.indexing_jobs
                  ) as indexing_last_attempt_at,
                  (
                    select count(*)::int
                    from public.indexing_jobs
                    where status in ('queued', 'running')
                  ) as indexing_queue_depth,
                  (
                    select max(last_computed_at)
                    from public.usage_period_snapshots
                  ) as maintenance_last_computed_at
                """
            )
        )
    ).mappings().one()
    workers = AdminWorkerStatus(
        indexing_last_success_at=worker_row["indexing_last_success_at"],
        indexing_last_attempt_at=worker_row["indexing_last_attempt_at"],
        indexing_queue_depth=int(worker_row["indexing_queue_depth"] or 0),
        maintenance_last_computed_at=worker_row["maintenance_last_computed_at"],
    )

    # Cost summary — reuse the cached Phase 3 platform overview so we don't run a
    # second full SUM(messages.*) for the home page.
    platform_costing = await get_platform_costing_overview(db, settings=settings)
    cur = platform_costing.current

    return AdminOverview(
        kpis=kpis,
        recent_signups=recent_signups,
        recent_conversations=recent_conversations,
        recent_stripe_events=recent_stripe_events,
        recent_indexing_failures=recent_indexing_failures,
        workers=workers,
        llm_cost_mtd_usd=cur.llm_cost_usd,
        embedding_cost_mtd_usd=cur.embedding_cost_usd,
        revenue_mtd_usd=cur.revenue_usd,
        gross_margin_mtd_usd=cur.gross_margin_usd,
        gross_margin_mtd_pct=cur.gross_margin_pct,
        pricing_unknown_models=list(platform_costing.unknown_models),
    )
