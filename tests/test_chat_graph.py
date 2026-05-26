"""LangGraph chat agent tests (Shopify tools + human escalation)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool

from app.agent.graph import (
    _route_after_model,
    _route_after_shopify_tools,
    append_escalation_tool_prompt,
    build_chat_graph,
)
from app.agent.knowledge_tools import SEARCH_KNOWLEDGE_BASE_TOOL_NAME, build_search_knowledge_base_tool
from app.agent.tools import ESCALATE_TO_HUMAN_TOOL_NAME, build_escalate_to_human_tool


def test_escalate_tool_name() -> None:
    tool = build_escalate_to_human_tool()
    assert tool.name == ESCALATE_TO_HUMAN_TOOL_NAME


def _shopify_tool(name: str) -> StructuredTool:
    async def _run(**kwargs: object) -> str:
        return "{}"

    return StructuredTool.from_function(
        coroutine=_run,
        name=name,
        description="test",
    )


def test_route_to_escalation_when_requested() -> None:
    ai = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "tc1",
                "name": ESCALATE_TO_HUMAN_TOOL_NAME,
                "args": {"reason": "Visitor asked for a human"},
            }
        ],
    )
    state = {
        "messages": [SystemMessage(content="sys"), HumanMessage(content="hi"), ai],
        "escalation_enabled": True,
        "shopify_tool_names": set(),
    }
    assert _route_after_model(state) == "escalation"


def test_route_to_tools_when_knowledge_search_bound() -> None:
    ai = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "tc_kb",
                "name": SEARCH_KNOWLEDGE_BASE_TOOL_NAME,
                "args": {"query": "girls frocks"},
            }
        ],
    )
    kb_tool = build_search_knowledge_base_tool(agent_id=uuid4(), min_similarity=0.2)
    state = {
        "messages": [ai],
        "escalation_enabled": False,
        "bound_tools": [kb_tool],
        "shopify_tool_names": set(),
    }
    assert _route_after_model(state) == "shopify_tools"


def test_route_to_shopify_tools_when_bound() -> None:
    ai = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "tc2",
                "name": "shopify_product_search",
                "args": {"query": "hoodie"},
            }
        ],
    )
    shop_tool = _shopify_tool("shopify_product_search")
    state = {
        "messages": [ai],
        "escalation_enabled": False,
        "bound_tools": [shop_tool],
        "shopify_tool_names": {"shopify_product_search"},
    }
    assert _route_after_model(state) == "shopify_tools"


def test_route_end_when_no_tools_enabled() -> None:
    ai = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "tc1",
                "name": ESCALATE_TO_HUMAN_TOOL_NAME,
                "args": {"reason": "x"},
            }
        ],
    )
    state = {"messages": [ai], "escalation_enabled": False, "shopify_tool_names": set()}
    # Unbound tool calls still route to the tools node so every call gets a ToolMessage reply.
    assert _route_after_model(state) == "shopify_tools"


def test_route_after_shopify_tools_loops_until_max_rounds() -> None:
    assert _route_after_shopify_tools({"model_round": 1}) == "call_model"
    assert _route_after_shopify_tools({"model_round": 5}) == "__end__"


def test_append_escalation_tool_prompt_only_when_enabled() -> None:
    base = "You are helpful."
    assert append_escalation_tool_prompt(base, tools_enabled=False) == base
    extended = append_escalation_tool_prompt(base, tools_enabled=True)
    assert "escalate_to_human" in extended


def test_handoff_reply_copy_is_visitor_clear() -> None:
    from app.agent.escalation import (
        handoff_reply_already_escalated,
        handoff_reply_awaiting_team,
        visitor_empty_reply_fallback,
    )

    already = handoff_reply_already_escalated()
    awaiting = handoff_reply_awaiting_team()
    empty = visitor_empty_reply_fallback()

    assert already == awaiting
    assert "Your message is with our team" in awaiting
    assert "importing" not in awaiting.lower()
    assert "importing" not in empty.lower()
    assert "support team" in empty


@pytest.mark.asyncio
async def test_stream_chat_graph_emits_done() -> None:
    from app.agent.escalation import EscalationTurnContext
    from app.agent.graph import stream_chat_graph

    user_id = uuid4()
    agent_id = uuid4()
    conversation_id = uuid4()

    async def _fake_astream(_initial, stream_mode=None):  # noqa: ANN001
        assert stream_mode == ["custom", "updates"]
        yield "custom", {"type": "token", "text": "Hello"}
        yield "updates", {
            "call_model": {
                "messages": [AIMessage(content="Hello")],
                "final_response": "Hello",
                "fallback_used": False,
                "usage_input_tokens": 1,
                "usage_output_tokens": 2,
                "tools_invoked": [],
                "escalation_occurred": False,
            }
        }

    mock_graph = MagicMock()
    mock_graph.astream = _fake_astream

    ctx = EscalationTurnContext(
        user_id=user_id,
        agent_id=agent_id,
        conversation_id=conversation_id,
        user_message="Hi",
        visitor_email=None,
        esc_cfg={},
    )
    messages = [SystemMessage(content="sys"), HumanMessage(content="Hi")]

    with patch("app.agent.graph.get_compiled_chat_graph", return_value=mock_graph):
        events = [
            e
            async for e in stream_chat_graph(
                messages=messages,
                model="gpt-4o-mini",
                temperature=0.0,
                fallback_message="fallback",
                escalation_enabled=False,
                bound_tools=[],
                turn_context=ctx,
            )
        ]

    assert events[0] == {"type": "token", "text": "Hello"}
    assert events[-1]["type"] == "done"
    assert events[-1]["response"] == "Hello"


def test_build_chat_graph_compiles() -> None:
    graph = build_chat_graph()
    assert graph is not None


def test_select_chunks_lexical_grounded_when_threshold_too_high() -> None:
    from app.domains.runtime.service import _rerank_chunks_for_query, _select_chunks_for_prompt

    merged = [
        {
            "id": "a",
            "content": "MEN-SALE – Breakout | https://breakout.com.pk/collections/men-sale",
            "similarity": 0.36,
        },
        {
            "id": "b",
            "content": (
                "Section: Everything New For Boys & Girls > Girls Collection > Fancy frocks\n\n"
                "Dressing up little girls in a fancy frock is a fun thing. "
                "Floral, fancy, glittery frocks and more."
            ),
            "similarity": 0.35,
        },
    ]
    chunks, mode, _, passed = _select_chunks_for_prompt(
        merged,
        user_message="do you sell frocks?",
        min_similarity=0.72,
    )
    assert passed == 0
    assert mode == "lexical_grounded_below_threshold"
    assert len(chunks) == 1
    assert chunks[0]["id"] == "b"

    ranked = _rerank_chunks_for_query(merged, "do you sell frocks?")
    assert ranked[0]["id"] == "b"


def test_extract_chunk_excerpt_finds_section_not_page_header() -> None:
    from app.domains.runtime.service import _build_context_block, _extract_query_terms

    chunk = {
        "content": (
            "Everything New For Boys & Girls – Breakout | https://example.com/blog\n\n"
            "### A well-curated wardrobe is all about versatility and timeless style.\n\n"
            + ("Intro filler paragraph. " * 80)
            + "\n\n## Girls Collection\n\n"
            "### Fancy frocks\n\n"
            "Dressing up little girls in a fancy frock is a fun thing. "
            "Designs such as floral, fancy, glittery, animated, pearl embellished, and embroidered."
        ),
    }
    excerpt = _build_context_block([chunk], user_message="what type of frocks?")
    assert "floral" in excerpt
    assert "versatility and timeless style" not in excerpt


def test_select_chunks_no_match_without_query_terms_in_candidates() -> None:
    from app.domains.runtime.service import _select_chunks_for_prompt

    merged = [
        {"id": "a", "content": "unrelated men sale collection", "similarity": 0.33},
    ]
    chunks, mode, _, _ = _select_chunks_for_prompt(
        merged,
        user_message="do you sell frocks?",
        min_similarity=0.72,
    )
    assert chunks == []
    assert mode == "no_match"
