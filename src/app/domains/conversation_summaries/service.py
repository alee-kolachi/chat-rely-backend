from __future__ import annotations

import json
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.settings import get_settings
from app.domains.conversation_outcomes.service import _format_transcript
from app.domains.conversation_summaries.schemas import (
    ConversationSummaryDTO,
    ConversationSummaryLLMResult,
    ConversationSummaryStateResponse,
)
from app.domains.conversations.service import get_conversation, list_messages

log = structlog.get_logger("conversation_summaries")

_MAX_KEY_POINTS = 8
_MAX_POINT_LEN = 200


def _count_transcript_messages(messages: list) -> int:
    n = 0
    for m in messages:
        role = getattr(m, "role", None) or ""
        if role in ("user", "assistant", "tool"):
            content = (getattr(m, "content", None) or "").strip()
            if role == "assistant":
                tcs = (getattr(m, "tool_call_payload", None) or {}).get("tool_calls")
                if content or (isinstance(tcs, list) and tcs):
                    n += 1
            elif content or role == "tool":
                n += 1
    return n


def _clip_points(points: list[str]) -> list[str]:
    out: list[str] = []
    for raw in points:
        label = (raw or "").strip()
        if len(label) < 2:
            continue
        out.append(label[:_MAX_POINT_LEN])
        if len(out) >= _MAX_KEY_POINTS:
            break
    return out


async def _fetch_summary_row(
    db: AsyncSession, user_id: UUID, conversation_id: UUID
) -> dict[str, object] | None:
    result = await db.execute(
        text(
            """
            select
              conversation_id,
              summary,
              key_points,
              message_count,
              computed_at,
              model
            from public.conversation_summaries
            where conversation_id = :conversation_id
              and user_id = :user_id
            """
        ),
        {"conversation_id": str(conversation_id), "user_id": str(user_id)},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def _invoke_summary_llm(transcript: str, conversation_status: str) -> tuple[ConversationSummaryLLMResult, str]:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    if not settings.openai_api_key:
        raise AppError(
            code="runtime.llm_not_configured",
            message="OPENAI_API_KEY is required to generate conversation summaries",
            status_code=500,
        )
    model_name = settings.openai_chat_model or "gpt-4o-mini"
    llm = ChatOpenAI(
        model=model_name,
        temperature=0,
        api_key=settings.openai_api_key,
        timeout=60,
        max_retries=1,
    )
    structured = llm.with_structured_output(ConversationSummaryLLMResult)
    sys = SystemMessage(
        content=(
            "You write a brief summary for a Shopify merchant reviewing a customer support chat. "
            "They need to understand the thread without reading every message. "
            "Be factual: only use the transcript. Do not invent orders, products, or policies. "
            "summary: 2–5 short sentences covering what the shopper wanted, what the assistant did, "
            "and the current outcome or open items. "
            "key_points: 3–6 tight bullet phrases (no full sentences required). "
            f"Conversation status: {conversation_status}."
        )
    )
    human = HumanMessage(content=f"Transcript:\n\n{transcript}")
    try:
        result = await structured.ainvoke([sys, human])
        if isinstance(result, ConversationSummaryLLMResult):
            return result, model_name
    except Exception as exc:
        log.warning("conversation_summary.llm_failed", error=str(exc))
    raise AppError(
        code="conversation.summary_generation_failed",
        message="Could not generate summary. Try again in a moment.",
        status_code=502,
    )


async def _upsert_summary(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    summary: str,
    key_points: list[str],
    message_count: int,
    model: str,
) -> ConversationSummaryDTO:
    points_json = json.dumps(key_points)
    result = await db.execute(
        text(
            """
            insert into public.conversation_summaries (
              conversation_id, agent_id, user_id, summary, key_points,
              message_count, computed_at, model
            ) values (
              :conversation_id, :agent_id, :user_id, :summary, cast(:key_points as jsonb),
              :message_count, now(), :model
            )
            on conflict (conversation_id) do update
              set summary = excluded.summary,
                  key_points = excluded.key_points,
                  message_count = excluded.message_count,
                  computed_at = now(),
                  model = excluded.model,
                  updated_at = now()
            returning
              conversation_id, summary, key_points, message_count, computed_at, model
            """
        ),
        {
            "conversation_id": str(conversation_id),
            "agent_id": str(agent_id),
            "user_id": str(user_id),
            "summary": summary,
            "key_points": points_json,
            "message_count": message_count,
            "model": model,
        },
    )
    row = result.mappings().one()
    raw_points = row["key_points"]
    if isinstance(raw_points, str):
        raw_points = json.loads(raw_points)
    points = [str(p) for p in (raw_points or []) if str(p).strip()]
    await db.commit()
    return ConversationSummaryDTO(
        conversation_id=row["conversation_id"],
        summary=str(row["summary"] or ""),
        key_points=points,
        computed_at=row["computed_at"],
        message_count=int(row["message_count"] or 0),
        stale=False,
        model=str(row["model"] or ""),
    )


def _row_to_state(
    conversation_id: UUID,
    row: dict[str, object] | None,
    *,
    current_message_count: int,
) -> ConversationSummaryStateResponse:
    if row is None:
        return ConversationSummaryStateResponse(
            conversation_id=conversation_id,
            message_count=current_message_count,
        )
    raw_points = row.get("key_points")
    if isinstance(raw_points, str):
        raw_points = json.loads(raw_points)
    points = [str(p) for p in (raw_points or []) if str(p).strip()]
    stored_count = int(row.get("message_count") or 0)
    return ConversationSummaryStateResponse(
        conversation_id=conversation_id,
        summary=str(row.get("summary") or "") or None,
        key_points=points,
        computed_at=row.get("computed_at"),  # type: ignore[arg-type]
        message_count=current_message_count,
        stale=current_message_count > stored_count,
        model=str(row.get("model") or ""),
    )


async def get_conversation_summary_state(
    db: AsyncSession,
    user_id: UUID,
    conversation_id: UUID,
) -> ConversationSummaryStateResponse:
    await get_conversation(db, user_id, conversation_id)
    messages = await list_messages(db, user_id, conversation_id)
    current_count = _count_transcript_messages(messages)
    row = await _fetch_summary_row(db, user_id, conversation_id)
    return _row_to_state(conversation_id, row, current_message_count=current_count)


async def generate_conversation_summary(
    db: AsyncSession,
    user_id: UUID,
    conversation_id: UUID,
    *,
    regenerate: bool = False,
) -> ConversationSummaryDTO:
    conv = await get_conversation(db, user_id, conversation_id)
    messages = await list_messages(db, user_id, conversation_id)
    current_count = _count_transcript_messages(messages)
    existing = await _fetch_summary_row(db, user_id, conversation_id)
    if existing and not regenerate:
        stored_count = int(existing.get("message_count") or 0)
        if current_count <= stored_count:
            state = _row_to_state(conversation_id, existing, current_message_count=current_count)
            return ConversationSummaryDTO(
                conversation_id=conversation_id,
                summary=state.summary or "",
                key_points=state.key_points,
                computed_at=state.computed_at,  # type: ignore[arg-type]
                message_count=current_count,
                stale=False,
                model=state.model,
            )

    transcript = _format_transcript(messages).strip()
    if not transcript:
        raise AppError(
            code="conversation.summary_empty_transcript",
            message="This conversation has no messages to summarize yet",
            status_code=400,
        )

    llm_result, model_used = await _invoke_summary_llm(transcript, conv.status)
    summary_text = (llm_result.summary or "").strip()
    if len(summary_text) < 10:
        raise AppError(
            code="conversation.summary_generation_failed",
            message="Generated summary was too short. Try again.",
            status_code=502,
        )
    return await _upsert_summary(
        db,
        user_id=user_id,
        agent_id=conv.agent_id,
        conversation_id=conversation_id,
        summary=summary_text[:2500],
        key_points=_clip_points(llm_result.key_points),
        message_count=current_count,
        model=model_used,
    )
