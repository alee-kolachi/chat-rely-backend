"""Cross-tenant agent reads for the admin panel.

The list view ships with the same filters the user-facing /api/v1/agents would, plus
owner email + cross-tenant aggregates. The detail view bundles everything an admin
might want to look at in one round-trip: settings, knowledge sources, enabled actions,
and the 20 most recent conversations.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.settings import Settings, get_settings
from app.domains.admin.costing import build_llm_cost_usd_expr
from app.domains.admin.schemas import (
    AdminAgentActionRow,
    AdminAgentDetail,
    AdminAgentListItem,
    AdminAgentListResponse,
    AdminConversationListItem,
    AdminKnowledgeSourceRow,
)


SortBy = Literal[
    "created_at",
    "name",
    "conversations_total",
    "conversations_mtd",
    "knowledge_sources_count",
]
SortDir = Literal["asc", "desc"]


# Hardcoded mapping prevents SQL injection via the sort_by query param.
_SORT_COLUMNS: dict[str, str] = {
    "created_at": "a.created_at",
    "name": "a.name",
    "conversations_total": "coalesce(ct.n, 0)",
    "conversations_mtd": "coalesce(cm.n, 0)",
    "knowledge_sources_count": "coalesce(ks_c.n, 0)",
}


def _resolve_order_by(sort_by: SortBy, sort_dir: SortDir) -> str:
    column = _SORT_COLUMNS.get(sort_by, _SORT_COLUMNS["created_at"])
    direction = "asc" if sort_dir == "asc" else "desc"
    return f"order by {column} {direction} nulls last, a.id asc"


async def list_admin_agents(
    db: AsyncSession,
    *,
    user_email: str | None = None,
    user_id: UUID | None = None,
    status: str | None = None,
    model: str | None = None,
    sort_by: SortBy = "created_at",
    sort_dir: SortDir = "desc",
    page: int = 1,
    page_size: int = 50,
) -> AdminAgentListResponse:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    offset = (page - 1) * page_size

    email_needle = (user_email or "").strip() or None
    where_clauses: list[str] = []
    params: dict[str, object] = {"limit": page_size, "offset": offset}

    if email_needle:
        where_clauses.append("u.email ilike '%' || :user_email || '%'")
        params["user_email"] = email_needle
    if user_id is not None:
        where_clauses.append("a.user_id = :user_id")
        params["user_id"] = str(user_id)
    if status:
        where_clauses.append("a.status = cast(:status as public.agent_status)")
        params["status"] = status
    if model:
        where_clauses.append("a.model = :model")
        params["model"] = model

    where_sql = ("where " + " and ".join(where_clauses)) if where_clauses else ""

    list_sql = f"""
        with conv_total as (
          select agent_id, count(*)::int as n
          from public.conversations
          group by agent_id
        ),
        conv_mtd as (
          select agent_id, count(*)::int as n
          from public.conversations
          where started_at >= date_trunc('month', now() at time zone 'UTC')
          group by agent_id
        ),
        ks_counts as (
          select agent_id, count(*)::int as n
          from public.knowledge_sources
          group by agent_id
        ),
        action_counts as (
          select agent_id, count(*)::int as n
          from public.agent_actions
          where enabled = true
          group by agent_id
        )
        select
          a.id, a.user_id, coalesce(u.email, '') as user_email,
          a.name, a.slug, a.status::text as status, a.model,
          coalesce(ct.n, 0) as conversations_total,
          coalesce(cm.n, 0) as conversations_mtd,
          coalesce(ks_c.n, 0) as knowledge_sources_count,
          coalesce(ac.n, 0) as actions_enabled_count,
          a.created_at, a.archived_at
        from public.agents a
        join auth.users u on u.id = a.user_id
        left join conv_total ct on ct.agent_id = a.id
        left join conv_mtd cm on cm.agent_id = a.id
        left join ks_counts ks_c on ks_c.agent_id = a.id
        left join action_counts ac on ac.agent_id = a.id
        {where_sql}
        {_resolve_order_by(sort_by, sort_dir)}
        limit :limit offset :offset
    """

    count_sql = f"""
        select count(*)::int as total
        from public.agents a
        join auth.users u on u.id = a.user_id
        {where_sql}
    """

    list_result = await db.execute(text(list_sql), params)
    items = [AdminAgentListItem.model_validate(r) for r in list_result.mappings().all()]

    count_params = {k: v for k, v in params.items() if k not in {"limit", "offset"}}
    count_result = await db.execute(text(count_sql), count_params)
    total = int(count_result.scalar_one())

    return AdminAgentListResponse(items=items, total=total, page=page, page_size=page_size)


async def get_admin_agent_detail(
    db: AsyncSession,
    agent_id: UUID,
    *,
    settings: Settings | None = None,
) -> AdminAgentDetail:
    settings = settings or get_settings()

    head = (
        await db.execute(
            text(
                """
                with conv_total as (
                  select count(*)::int as n
                  from public.conversations
                  where agent_id = :agent_id
                ),
                conv_mtd as (
                  select count(*)::int as n
                  from public.conversations
                  where agent_id = :agent_id
                    and started_at >= date_trunc('month', now() at time zone 'UTC')
                ),
                ks_count as (
                  select count(*)::int as n
                  from public.knowledge_sources
                  where agent_id = :agent_id
                ),
                action_count as (
                  select count(*)::int as n
                  from public.agent_actions
                  where agent_id = :agent_id and enabled = true
                )
                select
                  a.id, a.user_id, coalesce(u.email, '') as user_email,
                  a.name, a.slug, a.status::text as status, a.model,
                  a.system_prompt, a.behavior_settings, a.public_key,
                  a.created_at, a.archived_at,
                  (select n from conv_total) as conversations_total,
                  (select n from conv_mtd) as conversations_mtd,
                  (select n from ks_count) as knowledge_sources_count,
                  (select n from action_count) as actions_enabled_count
                from public.agents a
                join auth.users u on u.id = a.user_id
                where a.id = :agent_id
                """
            ),
            {"agent_id": str(agent_id)},
        )
    ).mappings().first()
    if head is None:
        raise AppError(code="admin.agent_not_found", message="Agent not found", status_code=404)

    knowledge_rows = (
        await db.execute(
            text(
                """
                select
                  ks.id, ks.user_id, coalesce(u.email, '') as user_email,
                  ks.agent_id, a.name as agent_name,
                  ks.type::text as type, ks.title, ks.status::text as status,
                  ks.source_url, ks.last_indexed_at, ks.error_message,
                  coalesce(c.cnt, 0)::int as chunks_count,
                  coalesce(c.tokens, 0)::int as chunks_total_tokens,
                  ks.created_at
                from public.knowledge_sources ks
                join auth.users u on u.id = ks.user_id
                join public.agents a on a.id = ks.agent_id
                left join lateral (
                  select count(*)::int as cnt, coalesce(sum(token_count), 0)::int as tokens
                  from public.knowledge_chunks
                  where knowledge_source_id = ks.id
                ) c on true
                where ks.agent_id = :agent_id
                order by ks.created_at desc
                """
            ),
            {"agent_id": str(agent_id)},
        )
    ).mappings().all()
    knowledge_sources = [AdminKnowledgeSourceRow.model_validate(r) for r in knowledge_rows]

    action_rows = (
        await db.execute(
            text(
                """
                select id, action_key, enabled, config, safety_policy, created_at, updated_at
                from public.agent_actions
                where agent_id = :agent_id
                order by action_key asc
                """
            ),
            {"agent_id": str(agent_id)},
        )
    ).mappings().all()
    actions = [
        AdminAgentActionRow.model_validate(
            {
                **dict(r),
                "config": r["config"] or {},
                "safety_policy": r["safety_policy"] or {},
            }
        )
        for r in action_rows
    ]

    llm_expr = build_llm_cost_usd_expr(
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
                    select sum({llm_expr})::float
                    from public.messages m
                    where m.conversation_id = c.id
                  ) as cost_usd
                from public.conversations c
                join public.agents a on a.id = c.agent_id
                join auth.users u on u.id = c.user_id
                where c.agent_id = :agent_id
                order by c.last_activity_at desc
                limit 20
                """
            ),
            {"agent_id": str(agent_id)},
        )
    ).mappings().all()
    recent_conversations = [
        AdminConversationListItem.model_validate(r) for r in convs_rows
    ]

    return AdminAgentDetail(
        id=head["id"],
        user_id=head["user_id"],
        user_email=head["user_email"],
        name=head["name"],
        slug=head["slug"],
        status=head["status"],
        model=head["model"],
        conversations_total=int(head["conversations_total"] or 0),
        conversations_mtd=int(head["conversations_mtd"] or 0),
        knowledge_sources_count=int(head["knowledge_sources_count"] or 0),
        actions_enabled_count=int(head["actions_enabled_count"] or 0),
        created_at=head["created_at"],
        archived_at=head["archived_at"],
        system_prompt=head["system_prompt"] or "",
        behavior_settings=head["behavior_settings"] or {},
        public_key=head["public_key"],
        knowledge_sources=knowledge_sources,
        actions=actions,
        recent_conversations=recent_conversations,
    )
