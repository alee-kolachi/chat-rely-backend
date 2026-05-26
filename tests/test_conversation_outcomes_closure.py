"""Regression tests for closure transcript shaping (training topics / outcomes)."""

from datetime import UTC, datetime
from uuid import uuid4

from app.domains.conversation_outcomes.service import (
    _closure_tool_result_summary,
    _format_transcript,
)
from app.domains.conversations.schemas import MessageDTO


def _msg(**kwargs: object) -> MessageDTO:
    base: dict[str, object] = {
        "id": str(uuid4()),
        "conversation_id": str(uuid4()),
        "agent_id": str(uuid4()),
        "user_id": "00000000-0000-0000-0000-000000000123",
        "role": "user",
        "content": "",
        "tool_name": None,
        "tool_call_id": None,
        "tool_call_payload": {},
        "tool_result_payload": {},
        "model": "gpt-4o-mini",
        "input_tokens": 0,
        "output_tokens": 0,
        "latency_ms": None,
        "metadata": {},
        "created_at": datetime.now(tz=UTC),
    }
    base.update(kwargs)
    return MessageDTO.model_validate(base)


def test_format_transcript_includes_tool_round_and_results() -> None:
    u1 = _msg(role="user", content="Is the blue deck in stock?")
    a_tools = _msg(
        role="assistant",
        content="",
        tool_call_payload={
            "tool_calls": [
                {"name": "shopify_product_search", "id": "c1", "args": {"q": "deck"}},
            ]
        },
    )
    t1 = _msg(role="tool", tool_name="shopify_product_search", content='{"variants":[]}')
    a1 = _msg(role="assistant", content="Yes, it is available.")
    u2 = _msg(role="user", content="What time do you open on Saturday?")
    a2 = _msg(role="assistant", content="I am not fully sure about store hours.")
    out = _format_transcript([u1, a_tools, t1, a1, u2, a2])
    assert "invoked tools: shopify_product_search" in out
    assert "returned structured data (success)" in out
    assert "Saturday" in out


def test_closure_tool_result_summary_errors() -> None:
    s = _closure_tool_result_summary('{"error": "timeout"}', "shopify_product_search")
    assert "error" in s.lower()
    assert "timeout" in s


def test_closure_tool_result_summary_non_json_truncates() -> None:
    long = "x" * 400
    s = _closure_tool_result_summary(long, None)
    assert "…" in s or len(s) < len(long) + 40
