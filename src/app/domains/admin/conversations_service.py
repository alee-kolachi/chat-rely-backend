"""Cross-tenant conversation reads for the admin panel.

Mirrors the user-facing list_conversations in `app.domains.conversations.service`
but without the `where c.user_id = :user_id` filter, plus joins to `agents` and
`auth.users` so each row carries the agent name + owner email.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.settings import Settings, get_settings
from app.domains.admin.costing import (
    build_llm_cost_usd_expr,
    compute_message_cost_usd,
)
from app.domains.admin.schemas import (
    AdminConversationDetail,
    AdminConversationListItem,
    AdminConversationListResponse,
    AdminMessageDTO,
)


# Hard cap on transcript size returned in a single detail call.
# Real conversations are far below this; the cap is a safety belt for runaway threads.
TRANSCRIPT_MESSAGE_CAP = 1000


async def list_admin_conversations(
    db: AsyncSession,
    *,
    user_id: UUID | None = None,
    user_email: str | None = None,
    agent_id: UUID | None = None,
    status: str | None = None,
    channel: str | None = None,
    visitor_id: str | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    escalated: bool | None = None,
    fallback_used: bool | None = None,
    page: int = 1,
    page_size: int = 50,
    settings: Settings | None = None,
) -> AdminConversationListResponse:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    offset = (page - 1) * page_size
    settings = settings or get_settings()

    email_needle = (user_email or "").strip() or None
    visitor_exact = (visitor_id or "").strip() or None

    where_clauses: list[str] = []
    params: dict[str, object] = {"limit": page_size, "offset": offset}

    if user_id is not None:
        where_clauses.append("c.user_id = :user_id")
        params["user_id"] = str(user_id)
    if email_needle:
        where_clauses.append("u.email ilike '%' || :user_email || '%'")
        params["user_email"] = email_needle
    if agent_id is not None:
        where_clauses.append("c.agent_id = :agent_id")
        params["agent_id"] = str(agent_id)
    if status:
        where_clauses.append("c.status = cast(:status as public.conversation_status)")
        params["status"] = status
    if channel:
        where_clauses.append("c.channel = cast(:channel as public.conversation_channel)")
        params["channel"] = channel
    if visitor_exact:
        where_clauses.append("c.visitor_id = :visitor_id")
        params["visitor_id"] = visitor_exact
    if started_after is not None:
        where_clauses.append("c.started_at >= :started_after")
        params["started_after"] = started_after
    if started_before is not None:
        where_clauses.append("c.started_at < :started_before")
        params["started_before"] = started_before
    if escalated is True:
        where_clauses.append("c.status = 'escalated'::public.conversation_status")
    if fallback_used is True:
        where_clauses.append("coalesce((c.metadata->>'fallback_used')::boolean, false) = true")
    elif fallback_used is False:
        where_clauses.append("coalesce((c.metadata->>'fallback_used')::boolean, false) = false")

    where_sql = ("where " + " and ".join(where_clauses)) if where_clauses else ""

    # Phase 3: per-conversation total cost from priced messages. NULL when no message
    # in the conversation has a priced model (matches AdminConversationListItem.cost_usd
    # nullability).
    llm_expr = build_llm_cost_usd_expr(
        settings.llm_input_price_per_million_usd,
        settings.llm_output_price_per_million_usd,
        alias="m",
    )

    list_sql = f"""
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
        {where_sql}
        order by c.last_activity_at desc
        limit :limit offset :offset
    """

    count_sql = f"""
        select count(*)::int as total
        from public.conversations c
        join public.agents a on a.id = c.agent_id
        join auth.users u on u.id = c.user_id
        {where_sql}
    """

    list_result = await db.execute(text(list_sql), params)
    items = [AdminConversationListItem.model_validate(row) for row in list_result.mappings().all()]

    count_params = {k: v for k, v in params.items() if k not in {"limit", "offset"}}
    count_result = await db.execute(text(count_sql), count_params)
    total = int(count_result.scalar_one())

    return AdminConversationListResponse(items=items, total=total, page=page, page_size=page_size)


async def get_admin_conversation_detail(
    db: AsyncSession,
    conversation_id: UUID,
    *,
    message_cap: int = TRANSCRIPT_MESSAGE_CAP,
    settings: Settings | None = None,
) -> AdminConversationDetail:
    settings = settings or get_settings()
    head_row = (
        await db.execute(
            text(
                """
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
                  c.closed_at, c.metadata
                from public.conversations c
                join public.agents a on a.id = c.agent_id
                join auth.users u on u.id = c.user_id
                where c.id = :conversation_id
                """
            ),
            {"conversation_id": str(conversation_id)},
        )
    ).mappings().first()
    if head_row is None:
        raise AppError(
            code="admin.conversation_not_found",
            message="Conversation not found",
            status_code=404,
        )

    total_messages = int(
        (
            await db.execute(
                text(
                    "select count(*)::int as n from public.messages where conversation_id = :conversation_id"
                ),
                {"conversation_id": str(conversation_id)},
            )
        ).scalar_one()
    )

    msg_rows = (
        await db.execute(
            text(
                """
                select
                  id, role, content, tool_name, tool_call_id,
                  tool_call_payload, tool_result_payload, model,
                  input_tokens, output_tokens, latency_ms, metadata, created_at
                from public.messages
                where conversation_id = :conversation_id
                order by created_at asc
                limit :cap
                """
            ),
            {"conversation_id": str(conversation_id), "cap": message_cap},
        )
    ).mappings().all()

    # Phase 3: per-message cost computed in Python so frontend only needs the float;
    # also rolls up to a conversation total used in the sidebar.
    messages: list[AdminMessageDTO] = []
    total_cost: float | None = None
    for row in msg_rows:
        cost = compute_message_cost_usd(
            settings,
            row["model"],
            int(row["input_tokens"] or 0),
            int(row["output_tokens"] or 0),
        )
        if cost is not None:
            total_cost = (total_cost or 0.0) + cost
        messages.append(
            AdminMessageDTO.model_validate({**dict(row), "cost_usd": cost})
        )
    truncated = total_messages > message_cap

    return AdminConversationDetail(
        id=head_row["id"],
        started_at=head_row["started_at"],
        last_activity_at=head_row["last_activity_at"],
        status=head_row["status"],
        channel=head_row["channel"],
        visitor_id=head_row["visitor_id"],
        agent_id=head_row["agent_id"],
        agent_name=head_row["agent_name"],
        user_id=head_row["user_id"],
        user_email=head_row["user_email"],
        customer_message_count=head_row["customer_message_count"],
        assistant_message_count=head_row["assistant_message_count"],
        tool_call_count=head_row["tool_call_count"],
        total_input_tokens=head_row["total_input_tokens"],
        total_output_tokens=head_row["total_output_tokens"],
        fallback_used=head_row["fallback_used"],
        latest_message_preview=head_row["latest_message_preview"],
        cost_usd=total_cost,
        closed_at=head_row["closed_at"],
        metadata=head_row["metadata"] or {},
        messages=messages,
        truncated=truncated,
        total_message_count=total_messages,
        transcript_message_cap=message_cap,
    )
