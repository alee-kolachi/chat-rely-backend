import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.domains.conversations.schemas import (
    ConversationDTO,
    ConversationMessageCreateRequest,
    ConversationUpdateRequest,
    MessageDTO,
)


async def get_conversation(db: AsyncSession, user_id: UUID, conversation_id: UUID) -> ConversationDTO:
    result = await db.execute(
        text(
            """
            select
              id, agent_id, user_id, visitor_id, channel, status, started_at, last_activity_at, closed_at,
              customer_message_count, assistant_message_count, tool_call_count,
              total_input_tokens, total_output_tokens, counts_toward_plan, metadata,
              created_at, updated_at
            from public.conversations
            where id = :conversation_id and user_id = :user_id
            """
        ),
        {"conversation_id": str(conversation_id), "user_id": str(user_id)},
    )
    row = result.mappings().first()
    if row is None:
        raise AppError(code="conversation.not_found", message="Conversation not found", status_code=404)
    return ConversationDTO.model_validate(row)


async def list_conversations(
    db: AsyncSession,
    user_id: UUID,
    agent_id: UUID | None = None,
    status: str | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    training_topic_slug: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ConversationDTO]:
    sql = """
        select
          c.id,
          c.agent_id,
          c.user_id,
          c.visitor_id,
          c.channel,
          c.status,
          c.started_at,
          c.last_activity_at,
          c.closed_at,
          c.customer_message_count,
          c.assistant_message_count,
          c.tool_call_count,
          c.total_input_tokens,
          c.total_output_tokens,
          c.counts_toward_plan,
          c.metadata,
          c.created_at,
          c.updated_at,
          (
            select left(m.content, 200)
            from public.messages m
            where m.conversation_id = c.id and m.user_id = c.user_id
            order by m.created_at desc
            limit 1
          ) as latest_message_preview
        from public.conversations c
        where c.user_id = :user_id
    """
    params: dict[str, object] = {"user_id": str(user_id), "limit": limit, "offset": offset}
    if agent_id:
        sql += " and c.agent_id = :agent_id"
        params["agent_id"] = str(agent_id)
    if status:
        sql += " and c.status = :status"
        params["status"] = status
    if started_after is not None:
        sql += " and c.started_at >= :started_after"
        params["started_after"] = started_after
    if started_before is not None:
        sql += " and c.started_at < :started_before"
        params["started_before"] = started_before
    if training_topic_slug and training_topic_slug.strip():
        sql += """
          and exists (
            select 1
            from public.conversation_outcomes co
            where co.conversation_id = c.id
              and exists (
                select 1
                from jsonb_array_elements(coalesce(co.payload->'training_topics', '[]'::jsonb)) elem
                where elem->>'slug' = :topic_slug
              )
          )
        """
        params["topic_slug"] = training_topic_slug.strip()
    sql += " order by c.last_activity_at desc limit :limit offset :offset"
    result = await db.execute(text(sql), params)
    return [ConversationDTO.model_validate(row) for row in result.mappings().all()]


async def list_messages(db: AsyncSession, user_id: UUID, conversation_id: UUID) -> list[MessageDTO]:
    await get_conversation(db, user_id, conversation_id)
    result = await db.execute(
        text(
            """
            select
              id, conversation_id, agent_id, user_id, role, content, tool_name, tool_call_id,
              tool_call_payload, tool_result_payload, model, input_tokens, output_tokens,
              latency_ms, metadata, created_at
            from public.messages
            where conversation_id = :conversation_id and user_id = :user_id
            order by created_at asc
            """
        ),
        {"conversation_id": str(conversation_id), "user_id": str(user_id)},
    )
    return [MessageDTO.model_validate(row) for row in result.mappings().all()]


async def list_messages_recent(
    db: AsyncSession,
    user_id: UUID,
    conversation_id: UUID,
    *,
    limit: int = 24,
    skip_conversation_check: bool = False,
) -> list[MessageDTO]:
    """Last N messages (chronological). Avoids loading entire threads on every chat turn."""
    if not skip_conversation_check:
        await get_conversation(db, user_id, conversation_id)
    lim = max(2, min(int(limit), 80))
    result = await db.execute(
        text(
            """
            select
              id, conversation_id, agent_id, user_id, role, content, tool_name, tool_call_id,
              tool_call_payload, tool_result_payload, model, input_tokens, output_tokens,
              latency_ms, metadata, created_at
            from public.messages
            where conversation_id = :conversation_id and user_id = :user_id
            order by created_at desc
            limit :lim
            """
        ),
        {
            "conversation_id": str(conversation_id),
            "user_id": str(user_id),
            "lim": lim,
        },
    )
    rows = list(result.mappings().all())
    rows.reverse()
    return [MessageDTO.model_validate(row) for row in rows]


async def append_message(
    db: AsyncSession,
    user_id: UUID,
    conversation_id: UUID,
    payload: ConversationMessageCreateRequest,
    *,
    agent_id: UUID | None = None,
) -> MessageDTO:
    conversation = await get_conversation(db, user_id, conversation_id)
    resolved_agent_id = agent_id or conversation.agent_id
    if agent_id is not None and conversation.agent_id != agent_id:
        from app.core.errors import AppError

        raise AppError(
            code="conversation.agent_mismatch",
            message="Conversation does not belong to this agent",
            status_code=403,
        )
    result = await db.execute(
        text(
            """
            insert into public.messages (
              conversation_id, agent_id, user_id, role, content, tool_name, tool_call_id,
              tool_call_payload, tool_result_payload, model, input_tokens, output_tokens, latency_ms, metadata
            ) values (
              :conversation_id, :agent_id, :user_id, :role, :content, :tool_name, :tool_call_id,
              CAST(:tool_call_payload AS jsonb), CAST(:tool_result_payload AS jsonb), :model,
              :input_tokens, :output_tokens, :latency_ms, CAST(:metadata AS jsonb)
            )
            returning
              id, conversation_id, agent_id, user_id, role, content, tool_name, tool_call_id,
              tool_call_payload, tool_result_payload, model, input_tokens, output_tokens,
              latency_ms, metadata, created_at
            """
        ),
        {
            "conversation_id": str(conversation_id),
            "agent_id": str(resolved_agent_id),
            "user_id": str(user_id),
            "role": payload.role,
            "content": payload.content,
            "tool_name": payload.tool_name,
            "tool_call_id": payload.tool_call_id,
            "tool_call_payload": json.dumps(payload.tool_call_payload or {}),
            "tool_result_payload": json.dumps(payload.tool_result_payload or {}),
            "model": payload.model,
            "input_tokens": payload.input_tokens,
            "output_tokens": payload.output_tokens,
            "latency_ms": payload.latency_ms,
            "metadata": json.dumps(payload.metadata or {}),
        },
    )
    message = MessageDTO.model_validate(result.mappings().one())

    counter_column = {
        "user": "customer_message_count",
        "assistant": "assistant_message_count",
        "tool": "tool_call_count",
    }.get(payload.role)
    if counter_column:
        await db.execute(
            text(
                f"""
                update public.conversations
                set {counter_column} = {counter_column} + 1,
                    total_input_tokens = total_input_tokens + :input_tokens,
                    total_output_tokens = total_output_tokens + :output_tokens,
                    last_activity_at = now(),
                    updated_at = now()
                where id = :conversation_id and user_id = :user_id
                """
            ),
            {
                "conversation_id": str(conversation_id),
                "user_id": str(user_id),
                "input_tokens": payload.input_tokens,
                "output_tokens": payload.output_tokens,
            },
        )
    await db.commit()

    from app.domains.tickets.service import sync_ticket_status_after_message

    await sync_ticket_status_after_message(
        db,
        user_id=user_id,
        conversation_id=conversation_id,
        message_role=payload.role,
    )

    return message


async def merge_client_context_metadata(
    db: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    locale: str | None,
    country_code: str | None,
) -> None:
    patch: dict[str, str] = {}
    if locale and (lo := locale.strip()[:64]):
        patch["locale"] = lo
    if country_code:
        cc = country_code.strip().upper()
        if len(cc) == 2 and cc.isalpha():
            patch["country_code"] = cc
    if not patch:
        return
    await db.execute(
        text(
            """
            update public.conversations
            set metadata = coalesce(metadata, '{}'::jsonb) || cast(:patch as jsonb),
                updated_at = now()
            where id = :conversation_id and user_id = :user_id
            """
        ),
        {
            "conversation_id": str(conversation_id),
            "user_id": str(user_id),
            "patch": json.dumps(patch),
        },
    )
    await db.commit()


# Set when a teammate sends an assistant message from the dashboard (or API); runtime skips the LLM.
OPERATOR_ENGAGED_META_KEY = "operator_engaged"


async def mark_conversation_operator_engaged(
    db: AsyncSession, user_id: UUID, conversation_id: UUID
) -> None:
    # Key is literal: bound params as jsonb_build_object keys make asyncpg raise
    # IndeterminateDatatypeError (could not determine data type of parameter $1).
    await db.execute(
        text(
            """
            update public.conversations
            set metadata = coalesce(metadata, '{}'::jsonb)
                || jsonb_build_object('operator_engaged', true),
                updated_at = now()
            where id = :conversation_id and user_id = :user_id
            """
        ),
        {
            "conversation_id": str(conversation_id),
            "user_id": str(user_id),
        },
    )
    await db.commit()


async def try_mark_conversation_counts_toward_plan(db: AsyncSession, conversation_id: UUID) -> None:
    """Sets counts_toward_plan when the conversation is terminal and had any measured activity."""
    await db.execute(
        text("select public.mark_conversation_counts_toward_plan(cast(:conversation_id as uuid))"),
        {"conversation_id": str(conversation_id)},
    )


async def update_conversation_status(
    db: AsyncSession,
    user_id: UUID,
    conversation_id: UUID,
    payload: ConversationUpdateRequest,
) -> ConversationDTO:
    result = await db.execute(
        text(
            """
            update public.conversations
            set status = cast(:status as public.conversation_status),
                closed_at = case
                  when cast(:status as public.conversation_status) in (
                    'resolved'::public.conversation_status,
                    'idle_closed'::public.conversation_status
                  ) then coalesce(closed_at, now())
                  else closed_at
                end,
                updated_at = now()
            where id = :conversation_id and user_id = :user_id
            returning
              id, agent_id, user_id, visitor_id, channel, status, started_at, last_activity_at, closed_at,
              customer_message_count, assistant_message_count, tool_call_count,
              total_input_tokens, total_output_tokens, counts_toward_plan, metadata,
              created_at, updated_at
            """
        ),
        {"status": payload.status, "conversation_id": str(conversation_id), "user_id": str(user_id)},
    )
    row = result.mappings().first()
    if row is None:
        raise AppError(code="conversation.not_found", message="Conversation not found", status_code=404)
    if payload.status in ("idle_closed", "resolved", "escalated"):
        await try_mark_conversation_counts_toward_plan(db, conversation_id)
    await db.commit()
    return ConversationDTO.model_validate(row)

