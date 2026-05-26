"""Runtime helpers: RAG retrieval, agent config, conversation resolution (used by `app.agent`)."""

import asyncio
import hashlib
import json
import re
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict
from uuid import UUID

import structlog
from langchain_core.messages import AIMessage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.settings import get_settings
from app.db.session import get_session_factory
from app.domains.actions.human_availability import seller_is_available_for_live_chat
from app.domains.actions.service import (
    get_human_escalation_for_runtime,
    list_enabled_shopify_actions_for_runtime,
)
from app.domains.billing.cost_events import (
    COST_KIND_EMBEDDING_RAG,
    COST_KIND_LLM_INTENT_FALLBACK,
    COST_KIND_LLM_MAIN,
    COST_KIND_LLM_SHOPIFY_ROUTER,
    COST_KIND_LLM_TURN_SIGNALS,
    COST_KIND_TOOL_SHOPIFY,
    record_cost_event,
)
from app.domains.billing.usage_gate import (
    refresh_plan_usage_snapshot_isolated,
    resolve_usage_throttle_tier_for_turn,
)
from app.domains.conversation_outcomes.schemas import TurnSignalsDTO
from app.domains.conversation_outcomes.service import compute_turn_signals
from app.domains.conversations.schemas import ConversationMessageCreateRequest
from app.domains.conversations.service import (
    OPERATOR_ENGAGED_META_KEY,
    append_message,
    get_conversation,
    list_messages,
    list_messages_recent,
    merge_client_context_metadata,
)
from app.domains.integrations.shopify.service import (
    get_cached_shopify_connection,
    is_shopify_disconnected_cached,
    load_shopify_connection_for_agent,
)
from app.domains.knowledge.service import _embed_texts, embed_texts_with_token_usage
from app.domains.runtime.prompts import (
    build_grounded_user_prompt,
    build_system_prompt,
)
from app.domains.runtime.schemas import (
    RuntimeChatRequest,
    RuntimeChatResponse,
)
from app.domains.runtime.shopify_lc_tools import build_shopify_langchain_tools
from app.domains.tickets.service import record_escalation, update_visitor_email_metadata

log = structlog.get_logger("runtime.service")

# After threshold passes, keep at most this many merged candidates; the model sees top N only.
RAG_MERGED_CHUNK_CAP = 20
RAG_PROMPT_CHUNK_COUNT = 4
RAG_PROMPT_EXCERPT_MAX_CHARS = 700
RAG_PROMPT_CONTEXT_MAX_CHARS = 2400
# Vector search returns top-k by distance only (no SQL similarity cutoff).
# With text-embedding-3-small, short queries vs long page chunks (title + URL + body) often
# score ~0.28–0.42 even for strong semantic matches; merchant defaults like 0.72 filter everything.
RAG_VECTOR_SEARCH_MIN_SCORE = 0.0
RAG_CHUNK_NOISE_FLOOR = 0.25
RAG_EMBED_CACHE_TTL_SECONDS = 900
RAG_EMBED_CACHE_MAX_ITEMS = 512

# Appended to system message when Shopify tools are bound. Overrides RAG-only “use fallback” behavior.
_SHOPIFY_TOOLS_RUNTIME_BLOCK = (
    "\n\n--- Shopify tools (enabled for this chat) ---\n"
    "You MUST call the relevant Shopify Admin tools when the shopper asks about **this store’s** catalog, "
    "products, SKUs, prices, stock, orders, shipping/tracking, or customer-specific store records.\n"
    "- **Products / catalog / “do you sell…” / availability**: call `shopify_product_search` with the customer’s "
    "question text as `query` (include product name or keywords).\n"
    "- **Order status / tracking / shipment**: call `shopify_order_lookup`. "
    "Pass `order_name_or_number` if they gave an order # or name; pass `customer_email` if they gave email. "
    "If neither exists yet, ask briefly for order number or email—then call the tool.\n"
    "- **Inventory / stock quantity**: call `shopify_inventory_check`.\n"
    "- **Customer history / past purchases**: call `shopify_customer_context` when you have their email.\n"
    "**Inventory safety:** Never state that a product is unavailable, out of stock, or not carried until "
    "you have results from the applicable Shopify tool (`shopify_product_search`, `shopify_inventory_check`, …). "
    "If you have not called the tool yet, reply briefly (e.g. “Let me check our catalog…”) and call the tool — "
    "do not deny inventory based on guesses or on knowledge-base excerpts alone.\n"
    "Knowledge base excerpts (if present) are **supplementary** marketing/site context; they do **not** replace "
    "live Shopify data for accurate SKU/order/inventory answers.\n"
    "Do **not** reply with the canned fallback (“not fully sure…” / escalate-only) for store-specific questions "
    "until you have **called the applicable tool(s)** at least once (unless the tool returned an error).\n"
)
_TOOL_RAG_SUPPLEMENT_FOR_TOOLS = (
    "\n\n---\n"
    "Reminder: Shopify tools are enabled. If this question is about products or orders **in the connected store**, "
    "the assistant must invoke the appropriate tool(s) before treating the answer as unknown or using only the "
    "fallback message above."
)

_embed_cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
_embed_cache_lock = asyncio.Lock()

_kb_index_cache: OrderedDict[str, tuple[float, bool]] = OrderedDict()
_kb_index_cache_lock = asyncio.Lock()
_runtime_config_cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_runtime_config_cache_lock = asyncio.Lock()


@dataclass(frozen=True)
class _TurnPrep:
    """Parallel prep result for one chat turn (no message-regex routing)."""

    history: list[Any]
    expanded_query: str
    tool_list: list[Any]
    shopify_setup_timings: dict[str, float]
    shopify_load_ms: float
    chunks: list[dict[str, Any]]
    rag_billing: dict[str, Any]
    retrieve_timing: dict[str, float]
    retrieve_wall_ms: float
    skip_kb_retrieval: bool
    kb_skip_reason: str
    thread_had_shopify: bool
    router_input_tokens: int
    router_output_tokens: int


async def _agent_has_indexed_knowledge(agent_id: UUID) -> bool:
    """Structural fact: agent has at least one chunk on a ready knowledge source."""
    key = str(agent_id)
    ttl = float(get_settings().runtime_agent_kb_index_cache_ttl_seconds)
    now = time.time()
    async with _kb_index_cache_lock:
        cached = _kb_index_cache.get(key)
        if cached and (now - cached[0]) < ttl:
            _kb_index_cache.move_to_end(key)
            return cached[1]
    sf = get_session_factory()
    async with sf() as s:
        row = (
            await s.execute(
                text(
                    """
                    select exists (
                      select 1
                      from public.knowledge_chunks c
                      join public.knowledge_sources ks on ks.id = c.knowledge_source_id
                      where ks.agent_id = cast(:aid as uuid)
                        and ks.status = 'ready'
                      limit 1
                    ) as has_kb
                    """
                ),
                {"aid": str(agent_id)},
            )
        ).mappings().first()
    has_kb = bool(row and row.get("has_kb"))
    async with _kb_index_cache_lock:
        _kb_index_cache[key] = (now, has_kb)
        _kb_index_cache.move_to_end(key)
        while len(_kb_index_cache) > 256:
            _kb_index_cache.popitem(last=False)
    return has_kb


def _structural_skip_kb_retrieval(
    *, agent_has_indexed_kb: bool, thread_had_shopify_tools: bool
) -> tuple[bool, str]:
    """Skip RAG from integration/thread facts only (no message regex)."""
    if not agent_has_indexed_kb:
        return True, "no_indexed_knowledge"
    if thread_had_shopify_tools:
        return True, "thread_shopify_tools"
    return False, "vector_retrieval"


async def _load_shopify_tools_fast(
    *,
    user_id: UUID,
    agent_id: UUID,
    conversation_id: UUID | None = None,
) -> tuple[list[Any], dict[str, float]]:
    """Load Shopify LangChain tools only (no router LLM)."""
    timings: dict[str, float] = {}
    if is_shopify_disconnected_cached(agent_id):
        timings["load_connection_ms"] = 0.0
        timings["list_actions_ms"] = 0.0
        timings["build_tools_ms"] = 0.0
        timings["router_llm_ms"] = 0.0
        return [], timings

    conn_pair = get_cached_shopify_connection(agent_id)
    sf = get_session_factory()
    async with sf() as s:
        if conn_pair is None:
            t0 = time.perf_counter()
            conn_pair = await load_shopify_connection_for_agent(
                s, user_id=user_id, agent_id=agent_id
            )
            timings["load_connection_ms"] = (time.perf_counter() - t0) * 1000.0
        else:
            timings["load_connection_ms"] = 0.0
        if not conn_pair:
            timings["list_actions_ms"] = 0.0
            timings["build_tools_ms"] = 0.0
            log.info(
                "runtime.shopify_setup_breakdown",
                conversation_id=str(conversation_id) if conversation_id else None,
                **{k: round(v, 2) for k, v in timings.items()},
            )
            return [], timings

        t1 = time.perf_counter()
        enabled_shopify = await list_enabled_shopify_actions_for_runtime(
            s, user_id=user_id, agent_id=agent_id
        )
        timings["list_actions_ms"] = (time.perf_counter() - t1) * 1000.0

        t2 = time.perf_counter()
        tool_list: list[Any] = []
        if enabled_shopify:
            keys = {e[0] for e in enabled_shopify}
            tool_list = build_shopify_langchain_tools(conn_pair[0], conn_pair[1], keys)
        timings["build_tools_ms"] = (time.perf_counter() - t2) * 1000.0
        timings["router_llm_ms"] = 0.0

    log.info(
        "runtime.shopify_setup_breakdown",
        conversation_id=str(conversation_id) if conversation_id else None,
        **{k: round(v, 2) for k, v in timings.items()},
    )
    return tool_list, timings


def _resolve_runtime_model(model: str) -> str:
    # Keep backward compatibility with legacy/open-ended model labels.
    settings = get_settings()
    raw = (model or "").strip()
    if not raw:
        return settings.runtime_default_chat_model or "gpt-4o-mini"
    normalized = raw.lower()
    legacy_aliases = {"gpt-3.5", "gpt-3.5-turbo", "gpt35", "gpt-35"}
    if normalized in legacy_aliases:
        return "gpt-4o-mini"
    if settings.runtime_prefer_fast_chat_model and normalized in {
        "gpt-4",
        "gpt-4o",
        "gpt-4-turbo",
        "gpt-4-turbo-preview",
        "gpt-4-1106-preview",
        "gpt-4-0125-preview",
    }:
        return settings.runtime_default_chat_model or "gpt-4o-mini"
    return raw


def _apply_usage_limit_model_downgrade(
    *,
    throttle_tier: str | None,
    model: str,
) -> str:
    """When conversations used exceed the plan included amount, use ``runtime_usage_limit_exceeded_model`` (env: ``RUNTIME_USAGE_LIMIT_EXCEEDED_MODEL``)."""
    if throttle_tier != "strong":
        return model
    cheap = (get_settings().runtime_usage_limit_exceeded_model or "gpt-4o-mini").strip() or "gpt-4o-mini"
    out = _resolve_runtime_model(cheap)
    base = _resolve_runtime_model(model)
    if out != base:
        log.info(
            "runtime.model_downgraded_usage_limit",
            throttle_tier=throttle_tier,
            requested_model=base,
            effective_model=out,
        )
    return out


def _build_open_chat_system_prompt(
    system_prompt: str,
    *,
    human_escalation_enabled: bool = False,
) -> str:
    base = (system_prompt or "").strip()
    escalation_hint = (
        " or offer to connect them with a human agent if that is available"
        if human_escalation_enabled
        else ". Do not mention human escalation or handoff"
    )
    guidance = (
        "You are a customer-support chatbot for this brand. No indexed excerpts were retrieved for this question, "
        "so do not invent catalog details, prices, or policies. "
        "Respond helpfully to greetings and small talk; for product or policy questions, keep answers short, "
        "acknowledge you don’t have their knowledge base context for this turn, and suggest what the customer could "
        f"ask next or where on the site they might look (without making up URLs){escalation_hint}. "
        "Use earlier messages in this thread for follow-ups when the user refers to something already discussed."
    )
    return f"{base}\n\n{guidance}".strip() if base else guidance


def _creativity_from_behavior(behavior: dict[str, Any]) -> float:
    raw = behavior.get("creativity", 0.5)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, v))


async def _load_agent_runtime_config(db: AsyncSession, user_id: UUID, agent_id: UUID) -> dict[str, Any]:
    key = f"{user_id}:{agent_id}"
    ttl = float(get_settings().runtime_agent_config_cache_ttl_seconds)
    now = time.time()
    if ttl > 0:
        async with _runtime_config_cache_lock:
            hit = _runtime_config_cache.get(key)
            if hit is not None and (now - hit[0]) < ttl:
                _runtime_config_cache.move_to_end(key)
                return dict(hit[1])
    result = await db.execute(
        text(
            """
            select
              a.id, a.name, a.model, a.system_prompt, a.behavior_settings,
              rs.min_retrieval_similarity, rs.fallback_message,
              exists (
                select 1
                from public.knowledge_chunks c
                join public.knowledge_sources ks on ks.id = c.knowledge_source_id
                where ks.agent_id = a.id
                  and ks.status = 'ready'
                limit 1
              ) as has_indexed_knowledge
            from public.agents a
            left join public.agent_reliability_settings rs on rs.agent_id = a.id and rs.user_id = a.user_id
            where a.id = :agent_id and a.user_id = :user_id
            """
        ),
        {"agent_id": str(agent_id), "user_id": str(user_id)},
    )
    row = result.mappings().first()
    if row is None:
        raise AppError(code="agent.not_found", message="Agent not found", status_code=404)
    behavior = row["behavior_settings"] or {}
    if not isinstance(behavior, dict):
        behavior = {}
    tone = behavior.get("tone", "professional")
    creativity = _creativity_from_behavior(behavior if isinstance(behavior, dict) else {})
    raw_agent_type = behavior.get("agent_type")
    agent_type = str(raw_agent_type).strip().lower() if isinstance(raw_agent_type, str) else "brand_support"
    fallback = row["fallback_message"] or (
        f"Hello! I'm {row['name']}. I can use your website knowledge, but I am not fully sure yet. "
        "Please clarify your request."
    )
    has_kb = bool(row.get("has_indexed_knowledge"))
    cfg = {
        "agent_name": row["name"],
        "model": row["model"] or "gpt-4o-mini",
        "system_prompt": row["system_prompt"] or "",
        "agent_type": agent_type,
        "tone": tone,
        "creativity": creativity,
        "min_retrieval_similarity": float(row["min_retrieval_similarity"] or 0.52),
        "fallback_message": fallback,
        "has_indexed_knowledge": has_kb,
    }
    async with _kb_index_cache_lock:
        _kb_index_cache[str(agent_id)] = (time.time(), has_kb)
        _kb_index_cache.move_to_end(str(agent_id))
    if ttl > 0:
        async with _runtime_config_cache_lock:
            _runtime_config_cache[key] = (now, cfg)
            _runtime_config_cache.move_to_end(key)
            while len(_runtime_config_cache) > 128:
                _runtime_config_cache.popitem(last=False)
    return cfg


class ResolvedConversation(TypedDict, total=False):
    id: UUID
    status: str
    metadata: dict[str, Any]
    is_new: bool


def _conversation_row(
    row: Any,
    *,
    default_status: str = "open",
) -> ResolvedConversation:
    meta = row.get("metadata") if isinstance(row, dict) else None
    if not isinstance(meta, dict):
        meta = {}
    status = str(row.get("status") or default_status)
    return ResolvedConversation(
        id=UUID(str(row["id"])),
        status=status,
        metadata=meta,
        is_new=False,
    )


async def _resolve_or_create_conversation(
    db: AsyncSession,
    user_id: UUID,
    agent_id: UUID,
    visitor_id: str,
    conversation_id: UUID | None,
    *,
    channel: str = "api",
) -> ResolvedConversation:
    if conversation_id:
        result = await db.execute(
            text(
                """
                select id, visitor_id, status, metadata
                from public.conversations
                where id = :conversation_id and user_id = :user_id and agent_id = :agent_id
                """
            ),
            {"conversation_id": str(conversation_id), "user_id": str(user_id), "agent_id": str(agent_id)},
        )
        row = result.mappings().first()
        if row is None:
            raise AppError(code="conversation.not_found", message="Conversation not found", status_code=404)
        if str(row["visitor_id"]) != visitor_id:
            raise AppError(
                code="conversation.forbidden",
                message="Conversation does not belong to this visitor",
                status_code=403,
            )
        return _conversation_row(row)

    result = await db.execute(
        text(
            """
            select id, status, metadata
            from public.conversations
            where user_id = :user_id
              and agent_id = :agent_id
              and visitor_id = :visitor_id
              and status = any(:reusable_statuses)
            order by created_at desc
            limit 1
            """
        ),
        {
            "user_id": str(user_id),
            "agent_id": str(agent_id),
            "visitor_id": visitor_id,
            "reusable_statuses": ["open", "escalated"],
        },
    )
    existing = result.mappings().first()
    if existing:
        return _conversation_row(existing)

    ch = (channel or "api").strip() or "api"
    if ch not in ("api", "widget"):
        ch = "api"
    try:
        created = await db.execute(
            text(
                """
                insert into public.conversations (agent_id, user_id, visitor_id, channel, status)
                values (:agent_id, :user_id, :visitor_id, :channel, 'open')
                returning id
                """
            ),
            {
                "agent_id": str(agent_id),
                "user_id": str(user_id),
                "visitor_id": visitor_id,
                "channel": ch,
            },
        )
        new_id = UUID(str(created.mappings().one()["id"]))
        # stream_chat uses short-lived sessions via _db_call; commit so the next session can load the row.
        await db.commit()
        return ResolvedConversation(id=new_id, status="open", metadata={}, is_new=True)
    except Exception as exc:
        from sqlalchemy.exc import IntegrityError

        if not isinstance(exc, IntegrityError):
            raise
        result = await db.execute(
            text(
                """
                select id, status, metadata
                from public.conversations
                where user_id = :user_id
                  and agent_id = :agent_id
                  and visitor_id = :visitor_id
                  and status = any(:reusable_statuses)
                order by created_at desc
                limit 1
                """
            ),
            {
                "user_id": str(user_id),
                "agent_id": str(agent_id),
                "visitor_id": visitor_id,
                "reusable_statuses": ["open", "escalated"],
            },
        )
        existing = result.mappings().first()
        if existing is None:
            raise
        return _conversation_row(existing)


def _embedding_vector_param(embedding: list[float]) -> str:
    return "[" + ",".join(f"{v:.10f}" for v in embedding) + "]"


async def _match_chunks_with_embedding(
    db: AsyncSession,
    agent_id: UUID,
    embedding: list[float],
    min_similarity: float,
    *,
    match_count: int = 8,
) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            """
            select id, knowledge_source_id, content, metadata, similarity
            from public.match_knowledge_chunks(
              :agent_id,
              CAST(:embedding AS vector),
              :match_count,
              :min_score
            )
            """
        ),
        {
            "agent_id": str(agent_id),
            "embedding": _embedding_vector_param(embedding),
            "match_count": match_count,
            "min_score": min_similarity,
        },
    )
    return [dict(row) for row in result.mappings().all()]


async def _embed_text_with_cache(text: str) -> tuple[list[float], int | None]:
    """Embed one query string; return (vector, api_tokens_or_none if cache hit)."""
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    now = time.time()
    async with _embed_cache_lock:
        hit = _embed_cache.get(key)
        if hit is not None and (now - hit[0]) < RAG_EMBED_CACHE_TTL_SECONDS:
            _embed_cache.move_to_end(key)
            return hit[1], None

    vectors, tokens = await embed_texts_with_token_usage([text])
    vector = vectors[0]
    async with _embed_cache_lock:
        _embed_cache[key] = (now, vector)
        _embed_cache.move_to_end(key)
        while len(_embed_cache) > RAG_EMBED_CACHE_MAX_ITEMS:
            _embed_cache.popitem(last=False)
    return vector, tokens if tokens else None


async def _retrieve_context(
    db: AsyncSession,
    agent_id: UUID,
    query_text: str,
    min_similarity: float,
    *,
    match_count: int = 8,
) -> list[dict[str, Any]]:
    text_q = (query_text or "").strip()
    if not text_q:
        return []
    embeddings = await _embed_texts([text_q])
    return await _match_chunks_with_embedding(
        db, agent_id, embeddings[0], min_similarity, match_count=match_count
    )


async def _retrieve_merged_chunks_for_message(
    db: AsyncSession,
    agent_id: UUID,
    *,
    user_message: str,
    expanded_query: str,
    min_similarity: float,
    match_count: int = 10,
    meta_timing: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Embed each distinct query string once, merge vector matches, and return prompt chunks.

    Vector search ranks by cosine distance with no SQL score cutoff. Chunks above
    ``min_similarity`` are preferred. If none pass (common when the merchant threshold is
    ~0.72), chunks that contain query terms and clear the noise floor are used instead.

    Returns ``(chunks, rag_billing)`` where ``rag_billing`` may include
    ``rag_embedding_*_tokens``, ``rag_fallback_mode``, and retrieval counts.
    """
    billing: dict[str, Any] = {
        "rag_embedding_raw_tokens": None,
        "rag_embedding_expanded_tokens": None,
    }
    msg = (user_message or "").strip()
    if not msg:
        return [], billing
    exp = (expanded_query or "").strip()
    t_embed = time.perf_counter()
    if exp and exp != msg:
        raw_pack, exp_pack = await asyncio.gather(
            _embed_text_with_cache(msg),
            _embed_text_with_cache(exp),
        )
        raw_embedding, raw_t = raw_pack
        expanded_embedding, exp_t = exp_pack
        billing["rag_embedding_raw_tokens"] = raw_t
        billing["rag_embedding_expanded_tokens"] = exp_t
    else:
        raw_embedding, raw_t = await _embed_text_with_cache(msg)
        billing["rag_embedding_raw_tokens"] = raw_t
        expanded_embedding = None
    if meta_timing is not None:
        meta_timing["embed_ms"] = (time.perf_counter() - t_embed) * 1000.0

    async def merged_at(floor: float) -> list[dict[str, Any]]:
        tasks = [
            _match_chunks_with_embedding(db, agent_id, raw_embedding, floor, match_count=match_count)
        ]
        if expanded_embedding is not None:
            tasks.append(
                _match_chunks_with_embedding(db, agent_id, expanded_embedding, floor, match_count=match_count)
            )
        parts = await asyncio.gather(*tasks)
        return _merge_chunks_by_best_similarity(parts)[:RAG_MERGED_CHUNK_CAP]

    t_db = time.perf_counter()
    merged = await merged_at(float(RAG_VECTOR_SEARCH_MIN_SCORE))
    if meta_timing is not None:
        meta_timing["retrieve_db_ms"] = (time.perf_counter() - t_db) * 1000.0

    chunks, rag_fallback_mode, retrieved_count, passed_n = _select_chunks_for_prompt(
        merged,
        user_message=msg,
        min_similarity=float(min_similarity),
    )
    billing["rag_fallback_mode"] = rag_fallback_mode
    billing["retrieved_count"] = retrieved_count
    billing["passed_threshold_count"] = passed_n
    return chunks, billing


def _merge_chunks_by_best_similarity(chunks_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Union several retrieval lists, keeping the strongest similarity per chunk id."""
    by_id: dict[Any, dict[str, Any]] = {}
    for lst in chunks_lists:
        for row in lst:
            cid = row.get("id")
            if cid is None:
                continue
            prev = by_id.get(cid)
            if prev is None or float(row.get("similarity") or 0) > float(prev.get("similarity") or 0):
                by_id[cid] = row
    return sorted(by_id.values(), key=lambda r: float(r.get("similarity") or 0), reverse=True)


def _extract_chunk_excerpt(
    text: str,
    query_terms: set[str],
    max_chars: int,
) -> str:
    """
    Pull the most query-relevant window from a page chunk.

    Indexed website chunks often start with title, URL, and a generic intro; the
    first ``max_chars`` bytes rarely contain the section the customer asked about.
    """
    cleaned = text.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned

    terms = query_terms or set()
    if not terms:
        head = cleaned[:max_chars].rstrip()
        return f"{head}..." if len(cleaned) > max_chars else head

    lines = cleaned.splitlines()

    def window_score(window: str) -> int:
        low = window.casefold()
        return sum(low.count(term) for term in terms)

    best = cleaned[:max_chars]
    best_score = window_score(best)

    for i, line in enumerate(lines):
        low = line.strip().casefold()
        if not any(term in low for term in terms):
            continue
        if not (
            low.startswith(("### ", "## ", "section:", "# "))
            or window_score(line) >= 2
        ):
            continue
        for span in (18, 28, 40):
            end = min(len(lines), i + span)
            window = "\n".join(lines[i:end])
            if len(window) > max_chars:
                lower = window.casefold()
                hit = min((lower.find(t) for t in terms if lower.find(t) >= 0), default=0)
                start = max(0, hit - max_chars // 5)
                window = window[start : start + max_chars]
            score = window_score(window)
            if score > best_score:
                best_score = score
                best = window

    lower_full = cleaned.casefold()
    for term in sorted(terms, key=len, reverse=True):
        pos = lower_full.find(term)
        if pos < 0:
            continue
        start = max(0, pos - max_chars // 5)
        window = cleaned[start : start + max_chars]
        score = window_score(window)
        if score > best_score:
            best_score = score
            best = window

    step = max(64, max_chars // 6)
    for start in range(0, len(cleaned), step):
        window = cleaned[start : start + max_chars]
        score = window_score(window)
        if score > best_score:
            best_score = score
            best = window

    out = best.strip()
    prefix = "..." if cleaned.find(out[: min(48, len(out))]) > 48 else ""
    suffix = "..." if len(out) >= max_chars - 3 else ""
    return f"{prefix}{out}{suffix}"


def _build_context_block(
    chunks: list[dict[str, Any]],
    *,
    user_message: str = "",
) -> str:
    query_terms = _extract_query_terms(user_message)
    lines: list[str] = []
    total = 0
    for idx, chunk in enumerate(chunks):
        text = str(chunk.get("content") or "").strip()
        if not text:
            continue
        excerpt = _extract_chunk_excerpt(text, query_terms, RAG_PROMPT_EXCERPT_MAX_CHARS)
        block = f"[Excerpt {idx + 1}]\n{excerpt}"
        if total + len(block) > RAG_PROMPT_CONTEXT_MAX_CHARS:
            break
        lines.append(block)
        total += len(block) + 2
    return "\n\n".join(lines)


def _log_retrieval_trace(
    *,
    conversation_id: UUID,
    agent_id: UUID,
    user_message: str,
    expanded_query: str,
    min_similarity: float,
    chunks: list[dict[str, Any]],
    prompt_chunks: list[dict[str, Any]],
    kb_retrieval_skipped: bool,
    rag_fallback_mode: str,
    retrieved_count: int | None = None,
    passed_threshold_count: int | None = None,
) -> None:
    preview: list[dict[str, Any]] = []
    for idx, chunk in enumerate(prompt_chunks[:5], start=1):
        metadata = dict(chunk.get("metadata") or {})
        preview.append(
            {
                "rank": idx,
                "chunk_id": str(chunk.get("id") or ""),
                "knowledge_source_id": str(chunk.get("knowledge_source_id") or ""),
                "similarity": round(float(chunk.get("similarity") or 0.0), 4),
                "url": metadata.get("url"),
                "title": metadata.get("title"),
                "snippet": str(chunk.get("content") or "").replace("\n", " ")[:220],
            }
        )
    passed_n = (
        int(passed_threshold_count)
        if passed_threshold_count is not None
        else sum(1 for c in chunks if float(c.get("similarity") or 0.0) >= float(min_similarity))
    )
    log.info(
        "runtime.retrieval_trace",
        conversation_id=str(conversation_id),
        agent_id=str(agent_id),
        effective_min_similarity=min_similarity,
        min_similarity=min_similarity,
        retrieved_count=retrieved_count if retrieved_count is not None else len(chunks),
        passed_threshold_count=passed_n,
        kb_retrieval_skipped=kb_retrieval_skipped,
        rag_fallback_mode=rag_fallback_mode,
        user_query=user_message,
        expanded_query=expanded_query,
        prompt_chunk_count=len(prompt_chunks),
        chunks=preview,
    )


def _langchain_messages_prompt_stats(messages: list[Any]) -> dict[str, Any]:
    """Rough prompt size for observability (actual tokenizer counts come from provider usage)."""
    total_chars = 0
    system_chars = 0
    non_system_n = 0
    for m in messages:
        c = getattr(m, "content", None)
        if isinstance(c, str):
            n = len(c)
        elif isinstance(c, list):
            n = len(json.dumps(c, ensure_ascii=False))
        else:
            n = 0
        total_chars += n
        if isinstance(m, SystemMessage):
            system_chars += n
        else:
            non_system_n += 1
    return {
        "llm_message_count": len(messages),
        "prompt_approx_chars": total_chars,
        "system_prompt_approx_chars": system_chars,
        "non_system_message_count": non_system_n,
    }


def _detach_usage_refresh_task(task: asyncio.Task[Any]) -> None:
    """Let usage snapshot finish without blocking the response path."""

    async def _drain() -> None:
        try:
            await task
        except Exception:
            pass

    asyncio.create_task(_drain())


def _log_runtime_turn_timing(
    *,
    conversation_id: UUID,
    shopify_load_ms: float,
    retrieve_wall_ms: float,
    retrieve_timing: dict[str, float],
    kb_skip_reason: str,
    thread_had_shopify: bool,
    skip_kb_retrieval: bool,
    assistant_latency_ms: int,
    first_token_ms: Any | None = None,
    prep_ms: float | None = None,
    pre_llm_ms: float | None = None,
    tools_bound_count: int | None = None,
    route_meta: dict[str, Any] | None = None,
    shopify_setup_breakdown: dict[str, float] | None = None,
    prompt_stats: dict[str, Any] | None = None,
    llm_prompt_tokens_total: int | None = None,
    llm_completion_tokens_total: int | None = None,
    router_llm_ms: float | None = None,
) -> None:
    payload: dict[str, Any] = {
        "conversation_id": str(conversation_id),
        "shopify_load_ms": round(shopify_load_ms, 2),
        "intent_ms": 0.0,
        "retrieve_wall_ms": round(retrieve_wall_ms, 2),
        "embed_ms": round(float(retrieve_timing.get("embed_ms", 0.0)), 2),
        "retrieve_db_ms": round(float(retrieve_timing.get("retrieve_db_ms", 0.0)), 2),
        "kb_skip_reason": kb_skip_reason,
        "thread_had_shopify_tools": thread_had_shopify,
        "kb_retrieval_skipped": skip_kb_retrieval,
        "total_turn_ms": assistant_latency_ms,
    }
    if router_llm_ms is not None:
        payload["router_llm_ms"] = round(float(router_llm_ms), 2)
    if first_token_ms is not None:
        payload["first_token_ms"] = round(float(first_token_ms), 2)
        payload["llm_ttft_ms"] = round(float(first_token_ms), 2)
    if prep_ms is not None:
        payload["prep_ms"] = round(float(prep_ms), 2)
    if pre_llm_ms is not None:
        payload["pre_llm_ms"] = round(float(pre_llm_ms), 2)
    if tools_bound_count is not None:
        payload["tools_bound_count"] = int(tools_bound_count)
    if route_meta:
        for k, v in route_meta.items():
            payload[f"route_{k}"] = v
    if shopify_setup_breakdown:
        for k, v in shopify_setup_breakdown.items():
            payload[f"shopify_{k}"] = round(float(v), 2)
    if prompt_stats:
        payload["llm_message_count"] = int(prompt_stats["llm_message_count"])
        payload["prompt_approx_chars"] = int(prompt_stats["prompt_approx_chars"])
        payload["system_prompt_approx_chars"] = int(prompt_stats["system_prompt_approx_chars"])
        payload["non_system_message_count"] = int(prompt_stats["non_system_message_count"])
    if llm_prompt_tokens_total is not None:
        payload["llm_prompt_tokens_total"] = int(llm_prompt_tokens_total)
    if llm_completion_tokens_total is not None:
        payload["llm_completion_tokens_total"] = int(llm_completion_tokens_total)
    log.info("runtime.turn_timing", **payload)
    if assistant_latency_ms >= int(get_settings().runtime_turn_latency_warn_ms):
        log.warning("runtime.turn_slow", **payload)


def _extract_query_terms(query_text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Z0-9]{4,}", (query_text or "").casefold())}


def _chunk_matches_query_terms(chunk: dict[str, Any], query_terms: set[str]) -> bool:
    if not query_terms:
        return True
    text = str(chunk.get("content") or "").casefold()
    return any(term in text for term in query_terms)


def _select_chunks_for_prompt(
    merged: list[dict[str, Any]],
    *,
    user_message: str,
    min_similarity: float,
) -> tuple[list[dict[str, Any]], str, int, int]:
    """
    Choose prompt chunks from ANN candidates.

    Prefer scores >= ``min_similarity``. When the merchant threshold is too high for this
    index (typical with page-sized chunks), fall back to chunks that contain query terms
    and are above ``RAG_CHUNK_NOISE_FLOOR`` — never inject unrelated top-k hits.
    """
    retrieved_count = len(merged)
    passed_threshold_count = sum(
        1 for r in merged if float(r.get("similarity") or 0.0) >= float(min_similarity)
    )
    candidates = [
        r for r in merged if float(r.get("similarity") or 0.0) >= float(RAG_CHUNK_NOISE_FLOOR)
    ]
    strict = [
        r for r in candidates if float(r.get("similarity") or 0.0) >= float(min_similarity)
    ]
    if strict:
        return (
            _rerank_chunks_for_query(strict, user_message)[:RAG_PROMPT_CHUNK_COUNT],
            "threshold",
            retrieved_count,
            passed_threshold_count,
        )

    query_terms = _extract_query_terms(user_message)
    if query_terms:
        grounded = [r for r in candidates if _chunk_matches_query_terms(r, query_terms)]
        if grounded:
            return (
                _rerank_chunks_for_query(grounded, user_message)[:RAG_PROMPT_CHUNK_COUNT],
                "lexical_grounded_below_threshold",
                retrieved_count,
                passed_threshold_count,
            )

    return [], "no_match", retrieved_count, passed_threshold_count


def _rerank_chunks_for_query(chunks: list[dict[str, Any]], query_text: str) -> list[dict[str, Any]]:
    if not chunks:
        return chunks
    query_terms = _extract_query_terms(query_text)
    if not query_terms:
        return sorted(chunks, key=lambda r: float(r.get("similarity") or 0.0), reverse=True)

    def score(row: dict[str, Any]) -> tuple[float, float]:
        sim = float(row.get("similarity") or 0.0)
        lex = float(row.get("lexical_score") or 0.0)
        text = str(row.get("content") or "").casefold()
        coverage = sum(1 for term in query_terms if term in text) / max(1, len(query_terms))
        occurrences = sum(text.count(term) for term in query_terms)
        repeat_bonus = min(0.06, 0.02 * max(0, occurrences - 1))
        heading_bonus = 0.0
        section_bonus = 0.0
        for line in text.splitlines():
            stripped = line.strip()
            low = stripped.casefold()
            if low.startswith("### ") and any(term in low for term in query_terms):
                heading_bonus = max(heading_bonus, 0.32)
            elif low.startswith("## ") and any(term in low for term in query_terms):
                heading_bonus = max(heading_bonus, 0.14)
            elif low.startswith("section:") and any(term in low for term in query_terms):
                section_bonus = max(section_bonus, 0.10)
        return (
            sim + (coverage * 0.22) + heading_bonus + section_bonus + repeat_bonus + (lex * 0.25),
            sim,
        )

    return sorted(chunks, key=score, reverse=True)


def _looks_like_fallback_response(answer: str, fallback_message: str) -> bool:
    a = (answer or "").strip().casefold()
    f = (fallback_message or "").strip().casefold()
    if not a:
        return True
    if f and a == f:
        return True
    return "not fully sure based on available information" in a


async def _record_embedding_rag_events(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    agent_id: UUID,
    user_id: UUID,
    turn_user_message_id: UUID,
    rag_billing: dict[str, Any],
) -> None:
    raw_t = rag_billing.get("rag_embedding_raw_tokens")
    if isinstance(raw_t, int) and raw_t > 0:
        await record_cost_event(
            db,
            conversation_id=conversation_id,
            agent_id=agent_id,
            user_id=user_id,
            kind=COST_KIND_EMBEDDING_RAG,
            turn_user_message_id=turn_user_message_id,
            embedding_tokens=raw_t,
            metadata={"variant": "raw_query"},
        )
    exp_t = rag_billing.get("rag_embedding_expanded_tokens")
    if isinstance(exp_t, int) and exp_t > 0:
        await record_cost_event(
            db,
            conversation_id=conversation_id,
            agent_id=agent_id,
            user_id=user_id,
            kind=COST_KIND_EMBEDDING_RAG,
            turn_user_message_id=turn_user_message_id,
            embedding_tokens=exp_t,
            metadata={"variant": "expanded_query"},
        )

