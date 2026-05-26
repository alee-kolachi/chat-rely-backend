"""SSE helpers; chat turns stream via ``app.agent.graph``."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import BaseMessage

from app.agent.llm import make_chat_model
from app.agent.messages import text_delta_from_stream_chunk, usage_tokens_from_model_message


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def stream_llm_sse(
    messages: list[BaseMessage],
    *,
    model: str,
    temperature: float,
    fallback_message: str,
) -> AsyncIterator[str]:
    llm = make_chat_model(model, temperature=temperature)
    parts: list[str] = []
    usage_in = 0
    usage_out = 0

    async for chunk in llm.astream(messages):
        delta = text_delta_from_stream_chunk(chunk)
        if delta:
            parts.append(delta)
            yield format_sse("token", {"text": delta})
        in_t, out_t = usage_tokens_from_model_message(chunk)
        if in_t or out_t:
            usage_in += in_t
            usage_out += out_t

    final = "".join(parts).strip()
    fallback_used = False
    if not final and fallback_message:
        final = fallback_message.strip()
        fallback_used = True
        yield format_sse("token", {"text": final})

    yield format_sse(
        "done",
        {
            "response": final,
            "fallback_used": fallback_used,
            "usage_input_tokens": usage_in,
            "usage_output_tokens": usage_out,
        },
    )
