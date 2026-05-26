"""Cross-tenant ticket reads for the admin panel.

Tickets always join `users` + `agents` + a linked `conversation` row. The detail view
embeds the conversation row so a single fetch is enough to render the page; the deep
transcript link still goes through `/admin/conversations/{id}` for the full message
history.
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
    AdminConversationListItem,
    AdminTicketDetail,
    AdminTicketListItem,
    AdminTicketListResponse,
)


SortBy = Literal["updated_at", "created_at", "priority", "status"]
SortDir = Literal["asc", "desc"]


_SORT_COLUMNS: dict[str, str] = {
    "updated_at": "t.updated_at",
    "created_at": "t.created_at",
    "priority": "t.priority",
    "status": "t.status",
}


def _resolve_order_by(sort_by: SortBy, sort_dir: SortDir) -> str:
    column = _SORT_COLUMNS.get(sort_by, _SORT_COLUMNS["updated_at"])
    direction = "asc" if sort_dir == "asc" else "desc"
    return f"order by {column} {direction} nulls last, t.id asc"


async def list_admin_tickets(
    db: AsyncSession,
    *,
    user_email: str | None = None,
    agent_id: UUID | None = None,
    status: str | None = None,
    priority: str | None = None,
    sort_by: SortBy = "updated_at",
    sort_dir: SortDir = "desc",
    page: int = 1,
    page_size: int = 50,
) -> AdminTicketListResponse:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    offset = (page - 1) * page_size

    email_needle = (user_email or "").strip() or None
    where_clauses: list[str] = []
    params: dict[str, object] = {"limit": page_size, "offset": offset}

    if email_needle:
        where_clauses.append("u.email ilike '%' || :user_email || '%'")
        params["user_email"] = email_needle
    if agent_id is not None:
        where_clauses.append("t.agent_id = :agent_id")
        params["agent_id"] = str(agent_id)
    if status:
        where_clauses.append("t.status = :status")
        params["status"] = status
    if priority:
        where_clauses.append("t.priority = :priority")
        params["priority"] = priority

    where_sql = ("where " + " and ".join(where_clauses)) if where_clauses else ""

    list_sql = f"""
        select
          t.id, t.user_id, coalesce(u.email, '') as user_email,
          t.agent_id, a.name as agent_name,
          t.conversation_id, t.status, t.priority,
          t.subject, t.customer_email, t.external_provider, t.external_id,
          t.created_at, t.updated_at
        from public.tickets t
        join auth.users u on u.id = t.user_id
        join public.agents a on a.id = t.agent_id
        {where_sql}
        {_resolve_order_by(sort_by, sort_dir)}
        limit :limit offset :offset
    """

    count_sql = f"""
        select count(*)::int as total
        from public.tickets t
        join auth.users u on u.id = t.user_id
        join public.agents a on a.id = t.agent_id
        {where_sql}
    """

    list_result = await db.execute(text(list_sql), params)
    items = [AdminTicketListItem.model_validate(r) for r in list_result.mappings().all()]

    count_params = {k: v for k, v in params.items() if k not in {"limit", "offset"}}
    count_result = await db.execute(text(count_sql), count_params)
    total = int(count_result.scalar_one())

    return AdminTicketListResponse(items=items, total=total, page=page, page_size=page_size)


async def get_admin_ticket_detail(
    db: AsyncSession,
    ticket_id: UUID,
    *,
    settings: Settings | None = None,
) -> AdminTicketDetail:
    settings = settings or get_settings()

    head = (
        await db.execute(
            text(
                """
                select
                  t.id, t.user_id, coalesce(u.email, '') as user_email,
                  t.agent_id, a.name as agent_name,
                  t.conversation_id, t.status, t.priority,
                  t.subject, t.customer_email, t.external_provider, t.external_id,
                  t.metadata, t.created_at, t.updated_at
                from public.tickets t
                join auth.users u on u.id = t.user_id
                join public.agents a on a.id = t.agent_id
                where t.id = :ticket_id
                """
            ),
            {"ticket_id": str(ticket_id)},
        )
    ).mappings().first()
    if head is None:
        raise AppError(code="admin.ticket_not_found", message="Ticket not found", status_code=404)

    llm_expr = build_llm_cost_usd_expr(
        settings.llm_input_price_per_million_usd,
        settings.llm_output_price_per_million_usd,
        alias="m",
    )
    conv_row = (
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
                where c.id = :conversation_id
                """
            ),
            {"conversation_id": str(head["conversation_id"])},
        )
    ).mappings().first()
    if conv_row is None:
        # Tickets enforce a FK to conversations, so this should be unreachable; treat as
        # a 500-class internal anomaly rather than masking it as a 404.
        raise AppError(
            code="admin.ticket_conversation_missing",
            message="Linked conversation not found for ticket",
            status_code=500,
        )
    conversation = AdminConversationListItem.model_validate(conv_row)

    return AdminTicketDetail(
        id=head["id"],
        user_id=head["user_id"],
        user_email=head["user_email"],
        agent_id=head["agent_id"],
        agent_name=head["agent_name"],
        conversation_id=head["conversation_id"],
        status=head["status"],
        priority=head["priority"],
        subject=head["subject"],
        customer_email=head["customer_email"],
        external_provider=head["external_provider"],
        external_id=head["external_id"],
        metadata=head["metadata"] or {},
        created_at=head["created_at"],
        updated_at=head["updated_at"],
        conversation=conversation,
    )
