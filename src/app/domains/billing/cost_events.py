"""Append-only conversation cost events for admin true-cost reporting."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.domains.admin.costing import compute_embedding_cost_usd, compute_message_cost_usd

COST_KIND_LLM_MAIN = "llm_main"
COST_KIND_LLM_ROUTING = "llm_routing"
COST_KIND_LLM_SHOPIFY_ROUTER = "llm_shopify_router"
COST_KIND_LLM_INTENT_FALLBACK = "llm_intent_fallback"
COST_KIND_LLM_TURN_SIGNALS = "llm_turn_signals"
COST_KIND_EMBEDDING_RAG = "embedding_rag"
COST_KIND_TOOL_SHOPIFY = "tool_shopify"


def _snapshot_cost_usd(
    settings: Settings,
    *,
    kind: str,
    provider_model: str | None,
    input_tokens: int,
    output_tokens: int,
    embedding_tokens: int,
) -> float | None:
    if kind == COST_KIND_TOOL_SHOPIFY:
        return 0.0
    if embedding_tokens > 0:
        return compute_embedding_cost_usd(settings, embedding_tokens)
    return compute_message_cost_usd(settings, provider_model, input_tokens, output_tokens)


async def record_cost_event(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    agent_id: UUID,
    user_id: UUID,
    kind: str,
    turn_user_message_id: UUID | None = None,
    provider_model: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    embedding_tokens: int = 0,
    metadata: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    if kind == COST_KIND_EMBEDDING_RAG and not (provider_model or "").strip():
        provider_model = (settings.openai_embedding_model or "").strip() or None

    cost_usd = _snapshot_cost_usd(
        settings,
        kind=kind,
        provider_model=provider_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        embedding_tokens=embedding_tokens,
    )
    meta = metadata if metadata is not None else {}

    await db.execute(
        text(
            """
            insert into public.conversation_cost_events (
              conversation_id, agent_id, user_id, turn_user_message_id,
              kind, provider_model, input_tokens, output_tokens, embedding_tokens,
              cost_usd, metadata
            ) values (
              :conversation_id, :agent_id, :user_id, :turn_user_message_id,
              :kind, :provider_model, :input_tokens, :output_tokens, :embedding_tokens,
              :cost_usd, cast(:metadata as jsonb)
            )
            """
        ),
        {
            "conversation_id": str(conversation_id),
            "agent_id": str(agent_id),
            "user_id": str(user_id),
            "turn_user_message_id": str(turn_user_message_id) if turn_user_message_id else None,
            "kind": kind,
            "provider_model": provider_model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "embedding_tokens": embedding_tokens,
            "cost_usd": cost_usd,
            "metadata": json.dumps(meta),
        },
    )
    await db.commit()
