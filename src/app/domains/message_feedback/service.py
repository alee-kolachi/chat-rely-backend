"""Visitor thumbs on assistant messages + Pro analytics summaries."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.settings import get_settings
from app.domains.message_feedback.schemas import (
    MessageFeedbackAnalyticsDTO,
    MessageFeedbackListItem,
)

log = structlog.get_logger("message_feedback")

# Playground / preview threads (not storefront visitors).
_PLAYGROUND_VISITOR_IDS = frozenset(
    {
        "preview-user",
        "playground-preview",
    }
)


def _playground_sql_filter(include_playground: bool) -> str:
    if include_playground:
        return ""
    ids = ", ".join(f"'{v}'" for v in sorted(_PLAYGROUND_VISITOR_IDS))
    return f" and c.visitor_id not in ({ids})"


async def delete_visitor_feedback(
    db: AsyncSession,
    *,
    agent_id: UUID,
    message_id: UUID,
    visitor_id: str,
    require_visitor_match: bool,
) -> None:
    """Remove this visitor's vote row (toggle off / revert)."""
    vid = visitor_id.strip()
    row = (
        await db.execute(
            text(
                """
                select m.id, c.visitor_id::text as conv_visitor
                from public.messages m
                join public.conversations c on c.id = m.conversation_id
                where m.id = cast(:mid as uuid)
                  and m.agent_id = cast(:aid as uuid)
                  and m.role = 'assistant'
                """
            ),
            {"mid": str(message_id), "aid": str(agent_id)},
        )
    ).mappings().first()
    if row is None:
        raise AppError(
            code="feedback.invalid_message",
            message="Assistant message not found for this agent.",
            status_code=404,
        )
    if require_visitor_match and str(row["conv_visitor"] or "").strip() != vid:
        raise AppError(
            code="feedback.visitor_mismatch",
            message="Visitor does not match this conversation.",
            status_code=403,
        )

    await db.execute(
        text(
            """
            delete from public.message_visitor_feedback
            where message_id = cast(:mid as uuid) and visitor_id = :vid
            """
        ),
        {"mid": str(message_id), "vid": vid},
    )
    await db.commit()


async def upsert_visitor_feedback(
    db: AsyncSession,
    *,
    agent_id: UUID,
    message_id: UUID,
    visitor_id: str,
    value: int,
    require_visitor_match: bool,
) -> None:
    """Insert or update a vote. Clears seller resolution on the message when value is thumbs-down."""
    vid = visitor_id.strip()
    row = (
        await db.execute(
            text(
                """
                select m.id, c.visitor_id::text as conv_visitor
                from public.messages m
                join public.conversations c on c.id = m.conversation_id
                where m.id = cast(:mid as uuid)
                  and m.agent_id = cast(:aid as uuid)
                  and m.role = 'assistant'
                """
            ),
            {"mid": str(message_id), "aid": str(agent_id)},
        )
    ).mappings().first()
    if row is None:
        raise AppError(
            code="feedback.invalid_message",
            message="Assistant message not found for this agent.",
            status_code=404,
        )
    if require_visitor_match and str(row["conv_visitor"] or "").strip() != vid:
        raise AppError(
            code="feedback.visitor_mismatch",
            message="Visitor does not match this conversation.",
            status_code=403,
        )

    await db.execute(
        text(
            """
            insert into public.message_visitor_feedback (message_id, visitor_id, value, updated_at)
            values (cast(:mid as uuid), :vid, :val, now())
            on conflict (message_id, visitor_id) do update
            set value = excluded.value,
                updated_at = now()
            """
        ),
        {"mid": str(message_id), "vid": vid, "val": value},
    )
    if value == -1:
        await db.execute(
            text(
                """
                update public.message_visitor_feedback
                set resolved_at = null, resolved_note = null, updated_at = now()
                where message_id = cast(:mid as uuid)
                """
            ),
            {"mid": str(message_id)},
        )
    await db.commit()


async def verify_message_belongs_to_user_agent(
    db: AsyncSession, *, user_id: UUID, agent_id: UUID, message_id: UUID
) -> None:
    row = (
        await db.execute(
            text(
                """
                select 1
                from public.messages m
                where m.id = cast(:mid as uuid)
                  and m.agent_id = cast(:aid as uuid)
                  and m.user_id = cast(:uid as uuid)
                  and m.role = 'assistant'
                """
            ),
            {"mid": str(message_id), "aid": str(agent_id), "uid": str(user_id)},
        )
    ).mappings().first()
    if row is None:
        raise AppError(
            code="feedback.invalid_message",
            message="Assistant message not found for this agent.",
            status_code=404,
        )


async def resolve_message_feedback(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    message_id: UUID,
    resolved: bool,
    note: str | None,
) -> None:
    await verify_message_belongs_to_user_agent(db, user_id=user_id, agent_id=agent_id, message_id=message_id)
    if resolved:
        await db.execute(
            text(
                """
                update public.message_visitor_feedback
                set resolved_at = now(),
                    resolved_note = :note,
                    updated_at = now()
                where message_id = cast(:mid as uuid)
                """
            ),
            {"mid": str(message_id), "note": (note or "").strip()[:2000] or None},
        )
        await delete_all_feedback_summaries_for_agent(db, agent_id=agent_id, user_id=user_id)
    else:
        await db.execute(
            text(
                """
                update public.message_visitor_feedback
                set resolved_at = null,
                    resolved_note = null,
                    updated_at = now()
                where message_id = cast(:mid as uuid)
                """
            ),
            {"mid": str(message_id)},
        )
        await delete_all_feedback_summaries_for_agent(db, agent_id=agent_id, user_id=user_id)
    await db.commit()


async def delete_all_feedback_summaries_for_agent(
    db: AsyncSession,
    *,
    agent_id: UUID,
    user_id: UUID,
) -> None:
    await db.execute(
        text(
            """
            delete from public.agent_feedback_summary
            where agent_id = cast(:aid as uuid)
              and user_id = cast(:uid as uuid)
            """
        ),
        {"aid": str(agent_id), "uid": str(user_id)},
    )


async def delete_feedback_summaries_for_range(
    db: AsyncSession,
    *,
    agent_id: UUID,
    user_id: UUID,
    range_from: datetime,
    range_to: datetime,
) -> None:
    await db.execute(
        text(
            """
            delete from public.agent_feedback_summary
            where agent_id = cast(:aid as uuid)
              and user_id = cast(:uid as uuid)
              and range_from = :rf
              and range_to = :rt
            """
        ),
        {"aid": str(agent_id), "uid": str(user_id), "rf": range_from, "rt": range_to},
    )
    await db.commit()


class _FeedbackSummaryLLM(BaseModel):
    model_config = {"extra": "forbid"}

    summary: str = Field(default="", max_length=4000)
    topics: list[str] = Field(default_factory=list)


def _clip(s: str, n: int) -> str:
    t = (s or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 1].rstrip() + "…"


async def _generate_batch_summary(
    *,
    prior_summary: str,
    new_excerpts: list[str],
    resolved_hints: list[str],
) -> tuple[str, list[str], str | None]:
    settings = get_settings()
    if not settings.openai_api_key:
        return "", [], None
    model = settings.openai_chat_model or "gpt-4o-mini"
    llm = ChatOpenAI(
        model=model,
        temperature=0,
        api_key=settings.openai_api_key,
        timeout=45,
        max_retries=1,
    )
    structured = llm.with_structured_output(_FeedbackSummaryLLM)
    excerpts_block = "\n".join(f"- {_clip(x, 500)}" for x in new_excerpts if x.strip())
    resolved_block = "\n".join(f"- {_clip(x, 300)}" for x in resolved_hints if x.strip())
    sys = SystemMessage(
        content=(
            "You summarize customer dissatisfaction with an AI storefront assistant. "
            "You receive a prior summary (may be empty), a batch of down-voted assistant replies, "
            "and optional notes about issues the merchant marked resolved — treat those as no longer open. "
            "Output a concise summary (2–6 sentences) and 3–8 short topic labels (noun phrases). "
            "Do not name individual customers; focus on product/service/content gaps."
        )
    )
    human = HumanMessage(
        content=(
            f"Prior summary:\n{prior_summary or '(none)'}\n\n"
            f"New down-voted assistant replies (batch):\n{excerpts_block or '(none)'}\n\n"
            f"Merchant-resolved themes (ignore as active issues):\n{resolved_block or '(none)'}\n"
        )
    )
    try:
        out = await structured.ainvoke([sys, human])
        if isinstance(out, _FeedbackSummaryLLM):
            topics = [t.strip() for t in out.topics if isinstance(t, str) and t.strip()][:12]
            return out.summary.strip(), topics, model
    except Exception as exc:
        log.warning("message_feedback.summary_failed", error=str(exc))
    return "", [], None


async def build_message_feedback_analytics(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    range_from: datetime,
    range_to: datetime,
    include_playground: bool,
) -> MessageFeedbackAnalyticsDTO:
    pf = _playground_sql_filter(include_playground)
    params: dict[str, object] = {
        "aid": str(agent_id),
        "uid": str(user_id),
        "rf": range_from,
        "rt": range_to,
    }

    counts_sql = f"""
        select
          coalesce(sum(case when f.value = 1 then 1 else 0 end), 0)::int as up_n,
          coalesce(sum(case when f.value = -1 and f.resolved_at is null then 1 else 0 end), 0)::int as down_open_n,
          coalesce(sum(case when f.value = -1 and f.resolved_at is not null then 1 else 0 end), 0)::int as down_resolved_n
        from public.message_visitor_feedback f
        join public.messages m on m.id = f.message_id
        join public.conversations c on c.id = m.conversation_id
        where m.agent_id = cast(:aid as uuid)
          and m.user_id = cast(:uid as uuid)
          and c.started_at >= :rf
          and c.started_at < :rt
          {pf}
    """
    crow = (await db.execute(text(counts_sql), params)).mappings().one()
    up_n = int(crow["up_n"] or 0)
    down_open_n = int(crow["down_open_n"] or 0)
    down_resolved_n = int(crow["down_resolved_n"] or 0)

    unresolved_sql = f"""
        select
          m.id as message_id,
          m.conversation_id,
          left(m.content, 400) as content_preview,
          f.visitor_id::text as visitor_id,
          f.updated_at as feedback_at
        from public.message_visitor_feedback f
        join public.messages m on m.id = f.message_id
        join public.conversations c on c.id = m.conversation_id
        where m.agent_id = cast(:aid as uuid)
          and m.user_id = cast(:uid as uuid)
          and f.value = -1
          and f.resolved_at is null
          and c.started_at >= :rf
          and c.started_at < :rt
          {pf}
        order by f.updated_at desc
        limit 80
    """
    urows = (await db.execute(text(unresolved_sql), params)).mappings().all()
    unresolved_items = [
        MessageFeedbackListItem(
            message_id=UUID(str(r["message_id"])),
            conversation_id=UUID(str(r["conversation_id"])),
            content_preview=str(r["content_preview"] or ""),
            visitor_id=str(r["visitor_id"] or ""),
            feedback_at=r["feedback_at"],
            resolved_at=None,
        )
        for r in urows
    ]

    resolved_sql = f"""
        select
          m.id as message_id,
          m.conversation_id,
          left(m.content, 400) as content_preview,
          min(f.visitor_id)::text as visitor_id,
          max(f.resolved_at) as resolved_at,
          max(f.updated_at) as feedback_at
        from public.message_visitor_feedback f
        join public.messages m on m.id = f.message_id
        join public.conversations c on c.id = m.conversation_id
        where m.agent_id = cast(:aid as uuid)
          and m.user_id = cast(:uid as uuid)
          and f.value = -1
          and f.resolved_at is not null
          and c.started_at >= :rf
          and c.started_at < :rt
          {pf}
        group by m.id, m.conversation_id, m.content
        order by max(f.resolved_at) desc
        limit 40
    """
    rrows = (await db.execute(text(resolved_sql), params)).mappings().all()
    resolved_items = [
        MessageFeedbackListItem(
            message_id=UUID(str(r["message_id"])),
            conversation_id=UUID(str(r["conversation_id"])),
            content_preview=str(r["content_preview"] or ""),
            visitor_id=str(r["visitor_id"] or ""),
            feedback_at=r["feedback_at"],
            resolved_at=r["resolved_at"],
        )
        for r in rrows
    ]

    # Ordered unresolved downvote events (one row per feedback row).
    order_sql = f"""
        select m.content::text as content
        from public.message_visitor_feedback f
        join public.messages m on m.id = f.message_id
        join public.conversations c on c.id = m.conversation_id
        where m.agent_id = cast(:aid as uuid)
          and m.user_id = cast(:uid as uuid)
          and f.value = -1
          and f.resolved_at is null
          and c.started_at >= :rf
          and c.started_at < :rt
          {pf}
        order by f.updated_at asc
    """
    orows = (await db.execute(text(order_sql), params)).mappings().all()
    ordered_excerpts = [str(r["content"] or "") for r in orows]
    n = len(ordered_excerpts)
    latest_batch_index: int | None = None
    summary: str | None = None
    topics: list[str] = []

    if n == 0:
        return MessageFeedbackAnalyticsDTO(
            thumbs_up_count=up_n,
            thumbs_down_unresolved_count=down_open_n,
            thumbs_down_resolved_count=down_resolved_n,
            unresolved_items=unresolved_items,
            resolved_items=resolved_items,
            summary=None,
            topics=[],
            latest_batch_index=None,
            playground_included=include_playground,
        )

    max_k = (n - 1) // 5
    for k in range(0, max_k + 1):
        cache_row = (
            await db.execute(
                text(
                    """
                    select summary, topics, model
                    from public.agent_feedback_summary
                    where agent_id = cast(:aid as uuid)
                      and user_id = cast(:uid as uuid)
                      and range_from = :rf
                      and range_to = :rt
                      and batch_index = :k
                    """
                ),
                {**params, "k": k},
            )
        ).mappings().first()
        if cache_row:
            latest_batch_index = k
            summary = str(cache_row["summary"] or "")
            raw_topics = cache_row["topics"]
            if isinstance(raw_topics, list):
                topics = [str(t) for t in raw_topics if str(t).strip()]
            elif isinstance(raw_topics, str):
                try:
                    topics = [str(t) for t in json.loads(raw_topics) if str(t).strip()]
                except Exception:
                    topics = []
            continue

        slice_excerpts = ordered_excerpts[5 * k : 5 * k + 5]
        prior = ""
        if k > 0:
            prow = (
                await db.execute(
                    text(
                        """
                        select summary
                        from public.agent_feedback_summary
                        where agent_id = cast(:aid as uuid)
                          and user_id = cast(:uid as uuid)
                          and range_from = :rf
                          and range_to = :rt
                          and batch_index = :pk
                        """
                    ),
                    {**params, "pk": k - 1},
                )
            ).mappings().first()
            if prow:
                prior = str(prow["summary"] or "")

        resolved_hints: list[str] = []
        if slice_excerpts:
            s_text, top, model_used = await _generate_batch_summary(
                prior_summary=prior,
                new_excerpts=slice_excerpts,
                resolved_hints=resolved_hints,
            )
            if model_used:
                await db.execute(
                    text(
                        """
                        insert into public.agent_feedback_summary (
                          agent_id, user_id, range_from, range_to, batch_index, summary, topics, model
                        )
                        values (
                          cast(:aid as uuid),
                          cast(:uid as uuid),
                          :rf, :rt,
                          :k,
                          :summary,
                          cast(:topics as jsonb),
                          :model
                        )
                        on conflict (agent_id, user_id, range_from, range_to, batch_index)
                        do update set
                          summary = excluded.summary,
                          topics = excluded.topics,
                          model = excluded.model,
                          created_at = now()
                        """
                    ),
                    {
                        "aid": str(agent_id),
                        "uid": str(user_id),
                        "rf": range_from,
                        "rt": range_to,
                        "k": k,
                        "summary": s_text,
                        "topics": json.dumps(top),
                        "model": model_used,
                    },
                )
                await db.commit()
                latest_batch_index = k
                summary = s_text or None
                topics = top

    return MessageFeedbackAnalyticsDTO(
        thumbs_up_count=up_n,
        thumbs_down_unresolved_count=down_open_n,
        thumbs_down_resolved_count=down_resolved_n,
        unresolved_items=unresolved_items,
        resolved_items=resolved_items,
        summary=summary,
        topics=topics,
        latest_batch_index=latest_batch_index,
        playground_included=include_playground,
    )


async def public_upsert_feedback(
    db: AsyncSession,
    *,
    agent_id: UUID,
    message_id: UUID,
    visitor_id: str,
    value: int | None,
    remove: bool,
) -> None:
    if remove:
        await delete_visitor_feedback(
            db,
            agent_id=agent_id,
            message_id=message_id,
            visitor_id=visitor_id,
            require_visitor_match=True,
        )
        return
    assert value is not None
    await upsert_visitor_feedback(
        db,
        agent_id=agent_id,
        message_id=message_id,
        visitor_id=visitor_id,
        value=value,
        require_visitor_match=True,
    )


async def owner_upsert_feedback(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    message_id: UUID,
    value: int | None,
    visitor_id: str | None,
    remove: bool,
) -> None:
    await verify_message_belongs_to_user_agent(db, user_id=user_id, agent_id=agent_id, message_id=message_id)
    vid = (visitor_id or "").strip() or f"owner:{user_id}"
    if remove:
        await delete_visitor_feedback(
            db,
            agent_id=agent_id,
            message_id=message_id,
            visitor_id=vid,
            require_visitor_match=False,
        )
        return
    assert value is not None
    await upsert_visitor_feedback(
        db,
        agent_id=agent_id,
        message_id=message_id,
        visitor_id=vid,
        value=value,
        require_visitor_match=False,
    )
