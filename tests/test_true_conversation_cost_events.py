"""Tests for conversation cost event ledger and Shopify tool-loop token accounting."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.core.settings import get_settings
from app.domains.admin import costing_service
from app.domains.admin.costing_service import get_conversation_cost
from app.domains.billing.cost_events import (
    COST_KIND_EMBEDDING_RAG,
    COST_KIND_LLM_MAIN,
    COST_KIND_TOOL_SHOPIFY,
    record_cost_event,
)
from app.domains.knowledge.service import _post_one_embedding_batch


def _head_result(conv_id: UUID) -> MagicMock:
    r = MagicMock()
    r.first.return_value = (str(conv_id),)
    return r


def _messages_result(rows: list[dict[str, Any]]) -> MagicMock:
    m = MagicMock()
    m.all.return_value = rows
    out = MagicMock()
    out.mappings.return_value = m
    return out


def _conv_meta_result(customer_message_count: int) -> MagicMock:
    m = MagicMock()
    m.first.return_value = {"customer_message_count": customer_message_count}
    out = MagicMock()
    out.mappings.return_value = m
    return out


def _events_result(rows: list[dict[str, Any]]) -> MagicMock:
    m = MagicMock()
    m.all.return_value = rows
    out = MagicMock()
    out.mappings.return_value = m
    return out


@pytest.mark.asyncio
async def test_record_cost_event_unknown_llm_model_snapshots_null_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_INPUT_PRICE_PER_MILLION_USD", '{"gpt-4o-mini":0.15}')
    monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_MILLION_USD", '{"gpt-4o-mini":0.60}')
    get_settings.cache_clear()

    captured: dict[str, Any] = {}

    async def _exec(stmt: Any, params: dict[str, Any] | None = None) -> None:
        captured.clear()
        captured.update(params or {})

    db = MagicMock()
    db.execute = _exec
    db.commit = AsyncMock()

    await record_cost_event(
        db,
        conversation_id=uuid4(),
        agent_id=uuid4(),
        user_id=uuid4(),
        kind=COST_KIND_LLM_MAIN,
        provider_model="unknown-xyz-model",
        input_tokens=500,
        output_tokens=200,
    )

    assert captured.get("cost_usd") is None
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_record_cost_event_tool_shopify_is_zero_usd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_INPUT_PRICE_PER_MILLION_USD", '{"gpt-4o-mini":0.15}')
    get_settings.cache_clear()

    captured: dict[str, Any] = {}

    async def _exec(_stmt: Any, params: dict[str, Any] | None = None) -> None:
        captured.clear()
        captured.update(params or {})

    db = MagicMock()
    db.execute = _exec
    db.commit = AsyncMock()

    await record_cost_event(
        db,
        conversation_id=uuid4(),
        agent_id=uuid4(),
        user_id=uuid4(),
        kind=COST_KIND_TOOL_SHOPIFY,
        metadata={"tool_name": "shopify_product_search"},
    )

    assert captured.get("cost_usd") == 0.0
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_get_conversation_cost_aggregates_events_by_kind_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_INPUT_PRICE_PER_MILLION_USD", '{"gpt-4o-mini":0.15}')
    monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_MILLION_USD", '{"gpt-4o-mini":0.60}')
    monkeypatch.setenv("EMBEDDING_PRICE_PER_MILLION_USD", '{"text-embedding-3-small":0.02}')
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    get_settings.cache_clear()
    costing_service._clear_cache()

    conv_id = uuid4()
    turn_id = uuid4()
    now = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
    msg_rows = [
        {
            "id": uuid4(),
            "role": "user",
            "model": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "created_at": now,
        },
        {
            "id": uuid4(),
            "role": "assistant",
            "model": "gpt-4o-mini",
            "input_tokens": 10,
            "output_tokens": 5,
            "created_at": now,
        },
    ]
    ev_rows = [
        {
            "id": uuid4(),
            "kind": COST_KIND_LLM_MAIN,
            "provider_model": "gpt-4o-mini",
            "turn_user_message_id": turn_id,
            "input_tokens": 10,
            "output_tokens": 5,
            "embedding_tokens": 0,
            "cost_usd": 0.001,
            "metadata": {},
            "created_at": now,
        },
        {
            "id": uuid4(),
            "kind": COST_KIND_EMBEDDING_RAG,
            "provider_model": "text-embedding-3-small",
            "turn_user_message_id": turn_id,
            "input_tokens": 0,
            "output_tokens": 0,
            "embedding_tokens": 500,
            "cost_usd": 0.00001,
            "metadata": {"cached": False},
            "created_at": now,
        },
    ]

    chain = iter(
        [
            _head_result(conv_id),
            _messages_result(msg_rows),
            _conv_meta_result(1),
            _events_result(ev_rows),
        ]
    )

    class _Sess:
        async def execute(self, _stmt: Any, _params: Any = None) -> Any:
            return next(chain)

    cost = await get_conversation_cost(_Sess(), conv_id)
    assert cost.events_total_cost_usd is not None
    assert abs(cost.events_total_cost_usd - 0.00101) < 1e-9
    assert cost.avg_cost_per_customer_message_usd is not None
    assert abs(cost.avg_cost_per_customer_message_usd - 0.00101) < 1e-9
    kinds = {k.kind: k for k in cost.by_kind}
    assert kinds[COST_KIND_LLM_MAIN].count == 1
    assert kinds[COST_KIND_EMBEDDING_RAG].count == 1
    assert len(cost.cost_events) == 2
    models = {m.model: m for m in cost.by_model}
    assert "gpt-4o-mini" in models
    assert models["gpt-4o-mini"].embedding_tokens == 0
    assert "text-embedding-3-small" in models
    assert models["text-embedding-3-small"].embedding_tokens == 500
    assert len(cost.per_turn) == 1
    assert cost.per_turn[0].turn_user_message_id == turn_id

    get_settings.cache_clear()
    costing_service._clear_cache()


@pytest.mark.asyncio
async def test_post_one_embedding_batch_reads_prompt_tokens_from_usage() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "data": [{"embedding": [0.1, 0.2]}],
        "usage": {"prompt_tokens": 17},
    }
    client.post = AsyncMock(return_value=response)
    settings = SimpleNamespace(openai_api_key="sk-test", openai_embedding_model="text-embedding-3-small")
    vectors, tokens = await _post_one_embedding_batch(
        client, ["hello"], allow_split=True, settings=settings
    )
    assert len(vectors) == 1
    assert tokens == 17


@pytest.mark.asyncio
async def test_post_one_embedding_batch_falls_back_to_total_tokens() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    payload = {"data": [{"embedding": [0.0]}], "usage": {"total_tokens": 99}}
    response.json.return_value = payload
    client.post = AsyncMock(return_value=response)
    settings = SimpleNamespace(openai_api_key="sk-test", openai_embedding_model="m")
    _vectors, tokens = await _post_one_embedding_batch(
        client, ["x"], allow_split=True, settings=settings
    )
    assert tokens == 99
