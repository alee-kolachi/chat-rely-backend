from __future__ import annotations

import json
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.settings import get_settings
from app.domains.conversation_outcomes.schemas import (
    ConversationOutcomeDTO,
    ConversationOutcomeLLMResult,
    ConversationOutcomePayload,
    OutcomeEndReason,
    TrainingTopicItem,
    TurnSignals,
    slugify_topic_label,
)
from app.domains.conversations.schemas import MessageDTO
from app.domains.conversations.service import get_conversation, list_messages
from app.agent.messages import usage_tokens_from_model_message

log = structlog.get_logger("conversation_outcomes")

# Top past intents shown to the closure model so it reuses stable slugs/labels.
INTENT_CATALOG_LIMIT = 10


async def fetch_agent_intent_catalog(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    exclude_conversation_id: UUID,
    limit: int = INTENT_CATALOG_LIMIT,
) -> list[tuple[str, str]]:
    """(slug, label) pairs by frequency desc, excluding the conversation being scored."""
    result = await db.execute(
        text(
            """
            select
              nullif(trim(o.payload->>'primary_intent_slug'), '') as slug,
              max(nullif(trim(o.payload->>'primary_intent'), '')) as label,
              count(*)::int as n
            from public.conversation_outcomes o
            join public.conversations c on c.id = o.conversation_id
            where c.user_id = cast(:user_id as uuid)
              and c.agent_id = cast(:agent_id as uuid)
              and o.conversation_id <> cast(:exclude as uuid)
              and length(coalesce(nullif(trim(o.payload->>'primary_intent_slug'), ''), '')) > 0
            group by 1
            order by n desc, slug asc
            limit :lim
            """
        ),
        {
            "user_id": str(user_id),
            "agent_id": str(agent_id),
            "exclude": str(exclude_conversation_id),
            "lim": limit,
        },
    )
    rows = result.mappings().all()
    out: list[tuple[str, str]] = []
    for r in rows:
        slug = str(r["slug"] or "").strip()
        if not slug:
            continue
        label = (str(r["label"] or slug).strip() or slug)[:200]
        out.append((slug, label))
    return out


def resolve_primary_intent_from_llm(
    llm_result: ConversationOutcomeLLMResult,
    catalog: list[tuple[str, str]],
) -> tuple[str, str]:
    """Map catalog / new-label fields to (primary_intent_slug, primary_intent)."""
    slug_to_label = {s.strip(): (lab or s).strip()[:200] for s, lab in catalog if s.strip()}
    by_lower = {s.lower(): s for s in slug_to_label}
    matched = (llm_result.matched_intent_slug or "").strip()
    if matched:
        canon = matched if matched in slug_to_label else by_lower.get(matched.lower())
        if canon is not None and canon in slug_to_label:
            return canon, slug_to_label[canon]
    new_label = (llm_result.new_intent_label or "").strip()[:200]
    if len(new_label) >= 2:
        return slugify_topic_label(new_label), new_label
    return "", ""


def _closure_tool_result_summary(content: str, tool_name: str | None) -> str:
    """Short line for closure analysis: signal success vs error without huge JSON."""
    name = (tool_name or "tool").strip() or "tool"
    raw = (content or "").strip()
    if not raw:
        return f"Tool ({name}): [empty]"
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        cut = 180
        one = raw.replace("\n", " ")[:cut]
        return f"Tool ({name}): {one}{'…' if len(raw) > cut else ''}"
    if isinstance(obj, dict) and obj.get("error") is not None:
        return f"Tool ({name}): error — {str(obj.get('error'))[:140]}"
    return f"Tool ({name}): returned structured data (success)"


def _format_transcript(messages: list[MessageDTO]) -> str:
    """User + assistant text plus compact tool-call context (not omitted at thread close)."""
    lines: list[str] = []
    for m in messages:
        if m.role == "user":
            lines.append(f"User: {(m.content or '').strip()}")
        elif m.role == "assistant":
            text_content = (m.content or "").strip()
            if text_content:
                lines.append(f"Assistant: {text_content}")
                continue
            tcs = (m.tool_call_payload or {}).get("tool_calls")
            if isinstance(tcs, list) and tcs:
                names: list[str] = []
                for c in tcs:
                    if isinstance(c, dict):
                        n = str(c.get("name") or "").strip()
                        if n:
                            names.append(n)
                if names:
                    lines.append(f"Assistant: [invoked tools: {', '.join(names)}]")
        elif m.role == "tool":
            lines.append(_closure_tool_result_summary(m.content, m.tool_name))
    return "\n".join(lines)


def _collect_turn_signal_blobs(messages: list[MessageDTO]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for m in messages:
        if m.role != "assistant":
            continue
        meta = m.metadata or {}
        ts = meta.get("turn_signals")
        if isinstance(ts, dict):
            out.append(ts)
    return out


async def _invoke_closure_llm(
    transcript: str,
    conversation_status: str,
    *,
    intent_catalog: list[tuple[str, str]],
) -> ConversationOutcomeLLMResult:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    if not settings.openai_api_key:
        raise AppError(
            code="runtime.llm_not_configured",
            message="OPENAI_API_KEY is required for conversation outcomes",
            status_code=500,
        )
    model_name = settings.openai_chat_model or "gpt-4o-mini"
    llm = ChatOpenAI(
        model=model_name,
        temperature=0,
        api_key=settings.openai_api_key,
        timeout=90,
        max_retries=2,
    )
    structured = llm.with_structured_output(ConversationOutcomeLLMResult)
    if intent_catalog:
        catalog_json = json.dumps(
            [{"slug": s, "label": lab} for s, lab in intent_catalog],
            ensure_ascii=False,
        )
        intent_rules = (
            "Intent catalog (reuse a stable slug when the transcript clearly fits one of these). "
            "Set matched_intent_slug to the EXACT slug string from the list when one clearly matches "
            "the customer's main goal. If none are a good fit, set matched_intent_slug to null and "
            "set new_intent_label to one short canonical phrase for a new intent (do not paraphrase "
            "an existing label when a list entry already fits). "
            "Only one of matched_intent_slug or new_intent_label should be non-null; prefer the catalog "
            "when uncertain between a close synonym and an existing entry."
        )
    else:
        catalog_json = "[]"
        intent_rules = (
            "No intent catalog exists yet for this agent. Set matched_intent_slug to null and "
            "new_intent_label to one short canonical phrase for the customer's main goal, or null if unclear."
        )

    sys = SystemMessage(
        content=(
            "You analyze a completed customer support chat. "
            "Decide if the shopper's issue was adequately addressed by the AI assistant before the thread ended. "
            "resolved_by_agent=true means the assistant gave a sufficient answer or path forward for that session, "
            "even if the user stopped replying without thanks (window_closed / user_abandoned). "
            "If the user left frustrated without a real fix, resolved_by_agent=false. "
            "escalated_to_human applies when handoff to humans was the correct outcome. "
            "training_topics: 0–5 short phrases for real KB/agent gaps — where the assistant lacked grounded facts, "
            "could not verify, gave only vague/generic help, refused without a substitute, or left the shopper's "
            "question effectively unanswered. "
            "Do NOT list topics the assistant already resolved well earlier in the thread (including after a "
            "successful tool-backed lookup shown in the transcript). "
            "If the shopper moved from one subject to another, weight the LATER messages heavily: prefer training "
            "labels that match unresolved concerns near the end of the thread, not the opening question alone. "
            "Return an empty list when there is no genuine gap. "
            f"{intent_rules} "
            f"Conversation status field from system: {conversation_status}."
        )
    )
    human = HumanMessage(
        content=(
            "Intent_catalog_json:\n"
            f"{catalog_json}\n\n"
            "Transcript:\n\n"
            f"{transcript}\n\n"
            "Return structured outcome fields only."
        )
    )
    result = await structured.ainvoke([sys, human])
    if not isinstance(result, ConversationOutcomeLLMResult):
        raise RuntimeError("structured output type mismatch")
    return result


async def compute_turn_signals(
    last_user_message: str, assistant_reply: str
) -> tuple[TurnSignals | None, int, int]:
    """Small side-call after each AI reply; stored on assistant message metadata.

    Returns ``(signals_or_none, input_tokens, output_tokens)``.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    if not settings.openai_api_key:
        return None, 0, 0
    if not settings.runtime_enable_turn_signals:
        return None, 0, 0
    llm = ChatOpenAI(
        model=settings.openai_chat_model or "gpt-4o-mini",
        temperature=0,
        api_key=settings.openai_api_key,
        timeout=30,
        max_retries=1,
    )
    structured = llm.with_structured_output(TurnSignals, include_raw=True)
    sys = SystemMessage(
        content=(
            "Given one user message and the assistant reply, classify briefly. "
            "knowledge_gap=true if the assistant lacked grounded facts/policy."
        )
    )
    human = HumanMessage(
        content=f"User: {last_user_message.strip()}\n\nAssistant: {assistant_reply.strip()}"
    )
    try:
        raw_out = await structured.ainvoke([sys, human])
        in_t, out_t = 0, 0
        if isinstance(raw_out, dict):
            raw_msg = raw_out.get("raw")
            parsed = raw_out.get("parsed")
            if raw_msg is not None:
                in_t, out_t = usage_tokens_from_model_message(raw_msg)
            if isinstance(parsed, TurnSignals):
                return parsed, in_t, out_t
            return None, in_t, out_t
        if isinstance(raw_out, TurnSignals):
            return raw_out, 0, 0
        return None, 0, 0
    except Exception as exc:
        log.warning("turn_signals.failed", error=str(exc))
        return None, 0, 0


def _fallback_payload(conversation_status: str) -> ConversationOutcomePayload:
    if conversation_status == "escalated":
        reason: OutcomeEndReason = "escalated_to_human"
        resolved = False
    elif conversation_status in ("resolved", "idle_closed"):
        reason = "other"
        resolved = True
    else:
        reason = "other"
        resolved = False
    return ConversationOutcomePayload(
        resolved_by_agent=resolved,
        resolution_confidence=0.0,
        end_reason=reason,
        evidence="Outcome analysis unavailable (LLM not configured or failed).",
        training_topics=[],
        needs_follow_up_training=False,
        primary_intent="",
        primary_intent_slug="",
    )


async def analyze_and_persist_outcome(
    db: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
) -> ConversationOutcomeDTO | None:
    conv = await get_conversation(db, user_id, conversation_id)
    if conv.status == "open":
        return None

    messages = await list_messages(db, user_id, conversation_id)
    transcript = _format_transcript(messages)
    if not transcript.strip():
        payload = ConversationOutcomePayload(
            resolved_by_agent=False,
            resolution_confidence=0.0,
            end_reason="other",
            evidence="No transcript.",
            training_topics=[],
            needs_follow_up_training=False,
            primary_intent="",
            primary_intent_slug="",
        )
        return await _upsert_outcome_row(
            db,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_id=conv.agent_id,
            model="",
            payload=payload,
        )

    turn_hints = _collect_turn_signal_blobs(messages)
    hint_block = ""
    if turn_hints:
        hint_block = "\nPer-turn model hints (may be noisy):\n" + json.dumps(turn_hints[:24])

    settings = get_settings()
    payload: ConversationOutcomePayload
    model_used = settings.openai_chat_model or "gpt-4o-mini"

    try:
        intent_catalog = await fetch_agent_intent_catalog(
            db,
            user_id=user_id,
            agent_id=conv.agent_id,
            exclude_conversation_id=conversation_id,
            limit=INTENT_CATALOG_LIMIT,
        )
        llm_result = await _invoke_closure_llm(
            transcript + hint_block,
            conv.status,
            intent_catalog=intent_catalog,
        )
        topics: list[TrainingTopicItem] = []
        seen: set[str] = set()
        for raw in llm_result.training_topics:
            label = (raw or "").strip()
            if len(label) < 2:
                continue
            slug = slugify_topic_label(label)
            if slug in seen:
                continue
            seen.add(slug)
            topics.append(TrainingTopicItem(slug=slug, label=label[:200]))

        intent_slug, intent_label = resolve_primary_intent_from_llm(llm_result, intent_catalog)

        payload = ConversationOutcomePayload(
            resolved_by_agent=llm_result.resolved_by_agent,
            resolution_confidence=llm_result.resolution_confidence,
            end_reason=llm_result.end_reason,
            evidence=(llm_result.evidence or "")[:4000],
            training_topics=topics,
            needs_follow_up_training=llm_result.needs_follow_up_training or bool(topics),
            primary_intent=intent_label,
            primary_intent_slug=intent_slug,
        )
    except Exception as exc:
        log.warning("closure_llm.failed", conversation_id=str(conversation_id), error=str(exc))
        payload = _fallback_payload(conv.status)
        model_used = ""

    return await _upsert_outcome_row(
        db,
        user_id=user_id,
        conversation_id=conversation_id,
        agent_id=conv.agent_id,
        model=model_used,
        payload=payload,
    )


async def _upsert_outcome_row(
    db: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    agent_id: UUID,
    model: str,
    payload: ConversationOutcomePayload,
) -> ConversationOutcomeDTO:
    payload_json = json.dumps(payload.model_dump(mode="json"))
    result = await db.execute(
        text(
            """
            insert into public.conversation_outcomes (
              conversation_id, agent_id, user_id, computed_at, model, payload
            ) values (
              :conversation_id, :agent_id, :user_id, now(), :model, cast(:payload as jsonb)
            )
            on conflict (conversation_id) do update
              set computed_at = now(),
                  model = excluded.model,
                  payload = excluded.payload,
                  updated_at = now()
            returning
              id, conversation_id, agent_id, user_id, computed_at, model, payload, created_at, updated_at
            """
        ),
        {
            "conversation_id": str(conversation_id),
            "agent_id": str(agent_id),
            "user_id": str(user_id),
            "model": model,
            "payload": payload_json,
        },
    )
    row = result.mappings().one()
    await db.commit()
    raw_payload = row["payload"]
    if isinstance(raw_payload, str):
        raw_payload = json.loads(raw_payload)
    return ConversationOutcomeDTO(
        id=row["id"],
        conversation_id=row["conversation_id"],
        agent_id=row["agent_id"],
        user_id=row["user_id"],
        computed_at=row["computed_at"],
        model=row["model"],
        payload=ConversationOutcomePayload.model_validate(raw_payload),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def close_idle_conversations_global(db: AsyncSession) -> int:
    """Runs DB idle closer (30-minute inactivity → idle_closed)."""
    result = await db.execute(text("select public.close_idle_conversations() as n"))
    row = result.mappings().one()
    n = int(row["n"])
    await db.commit()
    return n


async def process_pending_outcome_jobs(
    db: AsyncSession,
    *,
    limit: int = 15,
) -> int:
    """Analyze terminal conversations missing outcomes (any owner)."""
    result = await db.execute(
        text(
            """
            select c.id as conversation_id, c.user_id
            from public.conversations c
            left join public.conversation_outcomes o on o.conversation_id = c.id
            where c.status in ('idle_closed', 'resolved', 'escalated')
              and o.id is null
            order by c.updated_at asc
            limit :limit
            """
        ),
        {"limit": limit},
    )
    rows = result.mappings().all()
    processed = 0
    for row in rows:
        cid = UUID(str(row["conversation_id"]))
        uid = UUID(str(row["user_id"]))
        try:
            await analyze_and_persist_outcome(db, user_id=uid, conversation_id=cid)
            processed += 1
        except Exception as exc:
            log.warning("outcome.job_failed", conversation_id=str(cid), error=str(exc))
    return processed


async def tick_idle_and_outcomes(db: AsyncSession) -> tuple[int, int]:
    """Close idle threads then backfill outcomes. Returns (idle_closed_count, outcomes_processed)."""
    idle_n = await close_idle_conversations_global(db)
    outcome_n = await process_pending_outcome_jobs(db)
    return idle_n, outcome_n
