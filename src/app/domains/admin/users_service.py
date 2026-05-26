"""Cross-tenant user reads for the admin panel.

Reads `auth.users` directly (no RLS — backend connects as DB superuser; see Phase 1
plan for context). All routes that call into this service must be gated by
`require_admin`. No filtering by `auth.uid()` is performed.
"""

from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.settings import Settings, get_settings
from app.domains.admin.costing import (
    build_embedding_cost_usd_expr,
    build_llm_cost_usd_expr,
)
from app.domains.admin.schemas import (
    AdminAgentSummary,
    AdminConversationListItem,
    AdminKnowledgeSummary,
    AdminSubscriptionSummary,
    AdminUsageSnapshotSummary,
    AdminUserDetail,
    AdminUserListItem,
    AdminUserListResponse,
)


SortBy = Literal[
    "signed_up_at",
    "last_activity_at",
    "conversations_mtd",
    "email",
    "margin_mtd_usd",
    "total_cost_mtd_usd",
    "revenue_mtd_usd",
]
SortDir = Literal["asc", "desc"]


# Map allowed sort keys to the actual SQL column expression. Hardcoded mapping
# prevents SQL injection via the sort_by/sort_dir query params.
_SORT_COLUMNS: dict[str, str] = {
    "signed_up_at": "u.created_at",
    "last_activity_at": "la.t",
    "conversations_mtd": "coalesce(mc.n, 0)",
    "email": "u.email",
    # Phase 3: cost-based sorts. The columns are projected as named expressions in the
    # outer SELECT so we can sort on them by alias.
    "margin_mtd_usd": "margin_mtd_usd",
    "total_cost_mtd_usd": "total_cost_mtd_usd",
    "revenue_mtd_usd": "revenue_mtd_usd",
}


def _safe_margin_pct(margin: float, revenue: float) -> float | None:
    if revenue <= 0:
        return None
    return (margin / revenue) * 100.0


def _resolve_order_by(sort_by: SortBy, sort_dir: SortDir) -> str:
    column = _SORT_COLUMNS.get(sort_by, _SORT_COLUMNS["signed_up_at"])
    direction = "asc" if sort_dir == "asc" else "desc"
    # `nulls last` so users with no activity / no MTD don't dominate the top of a desc sort.
    return f"order by {column} {direction} nulls last, u.id asc"


async def list_admin_users(
    db: AsyncSession,
    *,
    search: str | None = None,
    sort_by: SortBy = "signed_up_at",
    sort_dir: SortDir = "desc",
    page: int = 1,
    page_size: int = 50,
    settings: Settings | None = None,
) -> AdminUserListResponse:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    offset = (page - 1) * page_size
    needle = (search or "").strip() or None
    settings = settings or get_settings()

    # Phase 3: per-user MTD cost CTEs. Built from env price maps; safe to inline because
    # `Settings.parse_price_map` validates model keys against [A-Za-z0-9._\-:].
    llm_expr = build_llm_cost_usd_expr(
        settings.llm_input_price_per_million_usd,
        settings.llm_output_price_per_million_usd,
        alias="m",
    )
    embedding_expr = build_embedding_cost_usd_expr(
        settings.embedding_price_per_million_usd,
        settings.openai_embedding_model,
        alias="kc",
    )

    list_sql = f"""
        with active_subs as (
          select distinct on (s.user_id)
            s.user_id, s.status, p.slug as plan_slug, p.name as plan_name,
            p.monthly_price_cents
          from public.subscriptions s
          join public.plans p on p.id = s.plan_id
          order by s.user_id, s.current_period_end desc
        ),
        agent_counts as (
          select user_id, count(*)::int as n from public.agents group by user_id
        ),
        mtd_convs as (
          select user_id, count(*)::int as n
          from public.conversations
          where started_at >= date_trunc('month', now() at time zone 'UTC')
          group by user_id
        ),
        last_activity as (
          select user_id, max(created_at) as t from public.messages group by user_id
        ),
        mtd_llm_cost as (
          select m.user_id, coalesce(sum({llm_expr}), 0.0)::float as cost
          from public.messages m
          where m.created_at >= date_trunc('month', now() at time zone 'UTC')
          group by m.user_id
        ),
        mtd_embed_cost as (
          select kc.user_id, coalesce(sum({embedding_expr}), 0.0)::float as cost
          from public.knowledge_chunks kc
          where kc.created_at >= date_trunc('month', now() at time zone 'UTC')
          group by kc.user_id
        )
        select
          u.id, coalesce(u.email, '') as email, p.full_name, u.created_at as signed_up_at,
          s.plan_slug, s.plan_name, s.status as subscription_status,
          coalesce(ac.n, 0) as agents_count,
          coalesce(mc.n, 0) as conversations_mtd,
          la.t as last_activity_at,
          -- Phase 3 cost columns (USD floats; cents-int loses precision at sub-penny scales).
          (case when s.user_id is null then 0.0
                else coalesce(s.monthly_price_cents, 0)::float / 100.0
           end) as revenue_mtd_usd,
          coalesce(ml.cost, 0.0)::float as llm_cost_mtd_usd,
          coalesce(me.cost, 0.0)::float as embedding_cost_mtd_usd,
          (coalesce(ml.cost, 0.0) + coalesce(me.cost, 0.0))::float as total_cost_mtd_usd,
          (
            (case when s.user_id is null then 0.0
                  else coalesce(s.monthly_price_cents, 0)::float / 100.0
             end)
            - (coalesce(ml.cost, 0.0) + coalesce(me.cost, 0.0))::float
          ) as margin_mtd_usd
        from auth.users u
        left join public.profiles p on p.id = u.id
        left join active_subs s on s.user_id = u.id
        left join agent_counts ac on ac.user_id = u.id
        left join mtd_convs mc on mc.user_id = u.id
        left join last_activity la on la.user_id = u.id
        left join mtd_llm_cost ml on ml.user_id = u.id
        left join mtd_embed_cost me on me.user_id = u.id
        where (
          cast(:search as text) is null
          or u.email ilike '%' || cast(:search as text) || '%'
          or coalesce(p.full_name, '') ilike '%' || cast(:search as text) || '%'
        )
        {_resolve_order_by(sort_by, sort_dir)}
        limit :limit offset :offset
    """

    count_sql = """
        select count(*)::int as total
        from auth.users u
        left join public.profiles p on p.id = u.id
        where (
          cast(:search as text) is null
          or u.email ilike '%' || cast(:search as text) || '%'
          or coalesce(p.full_name, '') ilike '%' || cast(:search as text) || '%'
        )
    """

    params = {"search": needle, "limit": page_size, "offset": offset}
    list_result = await db.execute(text(list_sql), params)
    raw_rows = list_result.mappings().all()

    items: list[AdminUserListItem] = []
    for row in raw_rows:
        # Pydantic builds margin_pct from the (revenue, margin) pair; SQL doesn't carry it
        # so we compute it once here to keep the validator side trivial.
        revenue = float(row["revenue_mtd_usd"] or 0.0)
        margin = float(row["margin_mtd_usd"] or 0.0)
        items.append(
            AdminUserListItem.model_validate(
                {
                    **dict(row),
                    "margin_pct_mtd": _safe_margin_pct(margin, revenue),
                }
            )
        )

    count_result = await db.execute(text(count_sql), {"search": needle})
    total = int(count_result.scalar_one())

    return AdminUserListResponse(items=items, total=total, page=page, page_size=page_size)


async def get_admin_user_detail(
    db: AsyncSession,
    user_id: UUID,
    *,
    settings: Settings | None = None,
) -> AdminUserDetail:
    settings = settings or get_settings()
    profile_row = (
        await db.execute(
            text(
                """
                select
                  u.id, coalesce(u.email, '') as email, u.created_at as signed_up_at,
                  p.full_name, p.avatar_url, coalesce(p.timezone, 'UTC') as timezone
                from auth.users u
                left join public.profiles p on p.id = u.id
                where u.id = :user_id
                """
            ),
            {"user_id": str(user_id)},
        )
    ).mappings().first()
    if profile_row is None:
        raise AppError(code="admin.user_not_found", message="User not found", status_code=404)

    subs_rows = (
        await db.execute(
            text(
                """
                select
                  s.id, p.slug as plan_slug, p.name as plan_name, p.monthly_price_cents,
                  s.status, s.provider, s.provider_customer_id, s.provider_subscription_id,
                  s.current_period_start, s.current_period_end, s.cancel_at_period_end, s.created_at
                from public.subscriptions s
                join public.plans p on p.id = s.plan_id
                where s.user_id = :user_id
                order by s.current_period_end desc, s.created_at desc
                """
            ),
            {"user_id": str(user_id)},
        )
    ).mappings().all()
    subscriptions = [AdminSubscriptionSummary.model_validate(r) for r in subs_rows]

    agents_rows = (
        await db.execute(
            text(
                """
                select
                  a.id, a.name, a.slug, a.model, a.status, a.created_at, a.archived_at,
                  coalesce(c_total.n, 0) as conversations_total,
                  coalesce(c_mtd.n, 0) as conversations_mtd
                from public.agents a
                left join lateral (
                  select count(*)::int as n
                  from public.conversations c
                  where c.agent_id = a.id
                ) c_total on true
                left join lateral (
                  select count(*)::int as n
                  from public.conversations c
                  where c.agent_id = a.id
                    and c.started_at >= date_trunc('month', now() at time zone 'UTC')
                ) c_mtd on true
                where a.user_id = :user_id
                order by a.created_at desc
                """
            ),
            {"user_id": str(user_id)},
        )
    ).mappings().all()
    agents = [AdminAgentSummary.model_validate(r) for r in agents_rows]

    convs_llm_expr = build_llm_cost_usd_expr(
        settings.llm_input_price_per_million_usd,
        settings.llm_output_price_per_million_usd,
        alias="m",
    )
    convs_rows = (
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
                    select sum({convs_llm_expr})::float
                    from public.messages m
                    where m.conversation_id = c.id
                  ) as cost_usd
                from public.conversations c
                join public.agents a on a.id = c.agent_id
                join auth.users u on u.id = c.user_id
                where c.user_id = :user_id
                order by c.last_activity_at desc
                limit 20
                """
            ),
            {"user_id": str(user_id)},
        )
    ).mappings().all()
    recent_conversations = [AdminConversationListItem.model_validate(r) for r in convs_rows]

    knowledge_rows = (
        await db.execute(
            text(
                """
                select coalesce(ks.type::text, 'unknown') as kind,
                       count(distinct ks.id)::int as sources,
                       coalesce(sum(kc.cnt), 0)::int as chunks
                from public.knowledge_sources ks
                left join lateral (
                  select count(*)::int as cnt
                  from public.knowledge_chunks c
                  where c.knowledge_source_id = ks.id
                ) kc on true
                where ks.user_id = :user_id
                group by ks.type
                """
            ),
            {"user_id": str(user_id)},
        )
    ).mappings().all()
    by_kind: dict[str, int] = {}
    total_sources = 0
    total_chunks = 0
    for row in knowledge_rows:
        kind = str(row["kind"])
        sources = int(row["sources"])
        chunks = int(row["chunks"])
        by_kind[kind] = sources
        total_sources += sources
        total_chunks += chunks
    knowledge_summary = AdminKnowledgeSummary(
        by_kind=by_kind,
        total_sources=total_sources,
        total_chunks=total_chunks,
    )

    snapshot_rows = (
        await db.execute(
            text(
                """
                select
                  id, period_start, period_end,
                  included_conversations, conversations_used, overage_conversations,
                  estimated_overage_cents, projected_conversations,
                  throttle_tier::text as throttle_tier,
                  coalesce(included_premium_turns, 0) as included_premium_turns,
                  coalesce(premium_turns_used, 0) as premium_turns_used,
                  last_computed_at
                from public.usage_period_snapshots
                where user_id = :user_id
                order by period_start desc
                limit 6
                """
            ),
            {"user_id": str(user_id)},
        )
    ).mappings().all()
    recent_usage_snapshots = [AdminUsageSnapshotSummary.model_validate(r) for r in snapshot_rows]

    # Phase 3 — single-row cost summary for the header KPI card. Per-agent breakdown
    # ships from the dedicated /users/{id}/costing endpoint to keep the detail page snappy.
    llm_expr = build_llm_cost_usd_expr(
        settings.llm_input_price_per_million_usd,
        settings.llm_output_price_per_million_usd,
        alias="m",
    )
    embedding_expr = build_embedding_cost_usd_expr(
        settings.embedding_price_per_million_usd,
        settings.openai_embedding_model,
        alias="kc",
    )
    cost_row = (
        await db.execute(
            text(
                f"""
                with revenue as (
                  select coalesce(p.monthly_price_cents, 0)::float / 100.0 as v
                  from public.subscriptions s
                  join public.plans p on p.id = s.plan_id
                  where s.user_id = :user_id
                    and s.status in ('active', 'trialing')
                  order by s.current_period_end desc
                  limit 1
                ),
                llm as (
                  select coalesce(sum({llm_expr}), 0.0)::float as v
                  from public.messages m
                  where m.user_id = :user_id
                    and m.created_at >= date_trunc('month', now() at time zone 'UTC')
                ),
                embed as (
                  select coalesce(sum({embedding_expr}), 0.0)::float as v
                  from public.knowledge_chunks kc
                  where kc.user_id = :user_id
                    and kc.created_at >= date_trunc('month', now() at time zone 'UTC')
                )
                select
                  coalesce((select v from revenue), 0.0)::float as revenue_mtd_usd,
                  coalesce((select v from llm), 0.0)::float as llm_cost_mtd_usd,
                  coalesce((select v from embed), 0.0)::float as embedding_cost_mtd_usd
                """
            ),
            {"user_id": str(user_id)},
        )
    ).mappings().one()
    revenue = float(cost_row["revenue_mtd_usd"])
    llm_cost = float(cost_row["llm_cost_mtd_usd"])
    embed_cost = float(cost_row["embedding_cost_mtd_usd"])
    total_cost = llm_cost + embed_cost
    margin = revenue - total_cost

    return AdminUserDetail(
        id=profile_row["id"],
        email=profile_row["email"],
        full_name=profile_row["full_name"],
        avatar_url=profile_row["avatar_url"],
        timezone=profile_row["timezone"],
        signed_up_at=profile_row["signed_up_at"],
        subscriptions=subscriptions,
        agents=agents,
        recent_conversations=recent_conversations,
        knowledge_summary=knowledge_summary,
        recent_usage_snapshots=recent_usage_snapshots,
        revenue_mtd_usd=revenue,
        llm_cost_mtd_usd=llm_cost,
        embedding_cost_mtd_usd=embed_cost,
        total_cost_mtd_usd=total_cost,
        margin_mtd_usd=margin,
        margin_pct_mtd=_safe_margin_pct(margin, revenue),
    )
