"""
LangGraph chat: model → Shopify tools (loop) or human escalation.

Streaming uses ``stream_mode="custom"`` for token and status events.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal, TypedDict
from uuid import UUID

import structlog
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import StreamWriter

from app.agent.escalation import (
    EscalationTurnContext,
    escalation_tool_system_appendix,
    handoff_reply_for_status,
    perform_escalation,
)
from app.agent.llm import make_chat_model
from app.agent.messages import text_delta_from_stream_chunk, text_from_model_message, usage_tokens_from_model_message
from app.agent.knowledge_tools import (
    is_knowledge_tool_name,
    knowledge_tool_status_message,
)
from app.agent.shopify_tools import (
    MAX_SHOPIFY_TOOL_ROUNDS,
    invoke_shopify_tool_with_timeout,
    is_shopify_tool_name,
    shopify_tool_status_message,
    tool_call_parts,
)
from app.agent.tools import ESCALATE_TO_HUMAN_TOOL_NAME, build_escalate_to_human_tool
from app.core.errors import AppError
from app.db.session import get_session_factory
from app.domains.conversations.service import get_conversation
from app.domains.runtime.shopify_lc_tools import tools_by_name

MAX_TOOL_ROUNDS = MAX_SHOPIFY_TOOL_ROUNDS

_log = structlog.get_logger("agent.graph")


class ChatGraphState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    model: str
    temperature: float
    fallback_message: str
    escalation_enabled: bool
    bound_tools: list[StructuredTool]
    shopify_tool_names: set[str]
    model_round: int
    tools_invoked: list[str]
    escalation_occurred: bool
    fallback_used: bool
    usage_input_tokens: int
    usage_output_tokens: int
    final_response: str
    turn_context: dict[str, Any]


def _last_ai_message(messages: list[BaseMessage]) -> AIMessage | None:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return msg
    return None


def _bound_tool_names(state: ChatGraphState) -> set[str]:
    return set(state.get("shopify_tool_names") or ())


def _collect_bound_tools(state: ChatGraphState) -> list[StructuredTool]:
    tools: list[StructuredTool] = list(state.get("bound_tools") or [])
    if state.get("escalation_enabled"):
        tools.append(build_escalate_to_human_tool())
    return tools


def _bound_tool_name_set(state: ChatGraphState) -> set[str]:
    return {
        str(getattr(t, "name", "") or "")
        for t in (state.get("bound_tools") or [])
        if getattr(t, "name", None)
    }


def _route_after_model(state: ChatGraphState) -> Literal["escalation", "shopify_tools", "__end__"]:
    ai = _last_ai_message(state.get("messages") or [])
    if ai is None or not (ai.tool_calls or []):
        return "__end__"

    names = {tool_call_parts(tc)[0] for tc in ai.tool_calls or []}
    if state.get("escalation_enabled") and ESCALATE_TO_HUMAN_TOOL_NAME in names:
        return "escalation"

    if names & _bound_tool_name_set(state):
        return "shopify_tools"
    # Model emitted tool calls we cannot run — still resolve them so the next model turn does not hang.
    if names:
        return "shopify_tools"
    return "__end__"


def _route_after_shopify_tools(state: ChatGraphState) -> Literal["call_model", "__end__"]:
    if int(state.get("model_round") or 0) >= MAX_TOOL_ROUNDS:
        return "__end__"
    return "call_model"


async def _call_model_node(state: ChatGraphState, writer: StreamWriter) -> dict[str, Any]:
    model_round = int(state.get("model_round") or 0)
    if model_round >= MAX_TOOL_ROUNDS:
        fallback_message = str(state.get("fallback_message") or "").strip()
        if fallback_message:
            writer({"type": "token", "text": fallback_message})
        return {
            "fallback_used": True,
            "final_response": fallback_message,
            "usage_input_tokens": int(state.get("usage_input_tokens") or 0),
            "usage_output_tokens": int(state.get("usage_output_tokens") or 0),
        }

    tools = _collect_bound_tools(state)
    llm = make_chat_model(state["model"], temperature=float(state.get("temperature") or 0.0))
    if tools:
        llm = llm.bind_tools(tools)

    parts: list[str] = []
    usage_in = int(state.get("usage_input_tokens") or 0)
    usage_out = int(state.get("usage_output_tokens") or 0)
    last_chunk: BaseMessage | None = None

    try:
        async for chunk in llm.astream(state["messages"]):
            last_chunk = chunk
            delta = text_delta_from_stream_chunk(chunk)
            if delta:
                parts.append(delta)
                writer({"type": "token", "text": delta})
            in_t, out_t = usage_tokens_from_model_message(chunk)
            usage_in = max(usage_in, in_t)
            usage_out = max(usage_out, out_t)
    except Exception as exc:
        raise AppError(
            code="runtime.llm_failed",
            message="LLM request failed",
            status_code=502,
            details={"error": str(exc)[:500]},
        ) from exc

    if last_chunk is not None and isinstance(last_chunk, AIMessage) and (last_chunk.tool_calls or []):
        return {
            "messages": [last_chunk],
            "model_round": model_round + 1,
            "fallback_used": False,
            "usage_input_tokens": usage_in,
            "usage_output_tokens": usage_out,
        }

    text = "".join(parts).strip()
    fallback_message = state.get("fallback_message") or ""
    fallback_used = False
    if not text and fallback_message:
        text = fallback_message.strip()
        fallback_used = True
        writer({"type": "token", "text": text})

    return {
        "messages": [AIMessage(content=text)],
        "model_round": model_round + 1,
        "fallback_used": fallback_used,
        "usage_input_tokens": usage_in,
        "usage_output_tokens": usage_out,
        "final_response": text,
    }


async def _escalation_node(state: ChatGraphState, writer: StreamWriter) -> dict[str, Any]:
    ai = _last_ai_message(state.get("messages") or [])
    if ai is None:
        return {}

    raw_ctx = state.get("turn_context") or {}
    ctx = EscalationTurnContext(
        user_id=raw_ctx["user_id"],
        agent_id=raw_ctx["agent_id"],
        conversation_id=raw_ctx["conversation_id"],
        user_message=str(raw_ctx.get("user_message") or ""),
        visitor_email=raw_ctx.get("visitor_email"),
        esc_cfg=dict(raw_ctx.get("esc_cfg") or {}),
    )

    reason = "Visitor requested human support."
    for tc in ai.tool_calls or []:
        name, args, _ = tool_call_parts(tc)
        if name != ESCALATE_TO_HUMAN_TOOL_NAME:
            continue
        if str(args.get("reason") or "").strip():
            reason = str(args["reason"]).strip()[:500]
        break

    async with get_session_factory()() as db:
        conv = await get_conversation(db, ctx.user_id, ctx.conversation_id)
        status = conv.status
        occurred = await perform_escalation(db, ctx=ctx)
        if occurred:
            status = "escalated"
        handoff = handoff_reply_for_status(conversation_status=status, esc_cfg=ctx.esc_cfg)

    writer({"type": "token", "text": handoff})
    tool_messages: list[BaseMessage] = []
    for tc in ai.tool_calls or []:
        _, _, tc_id = tool_call_parts(tc)
        tool_messages.append(
            ToolMessage(
                content=handoff,
                tool_call_id=tc_id,
                name=ESCALATE_TO_HUMAN_TOOL_NAME,
            )
        )

    prior = list(state.get("tools_invoked") or [])
    return {
        "messages": tool_messages + [AIMessage(content=handoff)],
        "tools_invoked": prior + [ESCALATE_TO_HUMAN_TOOL_NAME],
        "escalation_occurred": occurred,
        "final_response": handoff,
        "usage_input_tokens": int(state.get("usage_input_tokens") or 0),
        "usage_output_tokens": int(state.get("usage_output_tokens") or 0),
    }


async def _shopify_tools_node(state: ChatGraphState, writer: StreamWriter) -> dict[str, Any]:
    ai = _last_ai_message(state.get("messages") or [])
    if ai is None:
        return {}

    bound = list(state.get("bound_tools") or [])
    by_name = tools_by_name(bound)
    shopify_names = _bound_tool_names(state)
    raw_ctx = state.get("turn_context") or {}
    conversation_id = raw_ctx.get("conversation_id")
    conv_uuid = UUID(str(conversation_id)) if conversation_id else None
    model_round = int(state.get("model_round") or 0)

    tool_messages: list[BaseMessage] = []
    invoked = list(state.get("tools_invoked") or [])

    for tc in ai.tool_calls or []:
        name, args, tc_id = tool_call_parts(tc)
        if not name:
            continue
        if name not in by_name:
            tool_messages.append(
                ToolMessage(
                    content=json.dumps({"error": "tool_not_available", "tool": name}),
                    tool_call_id=tc_id,
                    name=name,
                )
            )
            continue
        if is_shopify_tool_name(name):
            if name not in shopify_names:
                tool_messages.append(
                    ToolMessage(
                        content=json.dumps({"error": "shopify_tool_disabled", "tool": name}),
                        tool_call_id=tc_id,
                        name=name,
                    )
                )
                continue
            writer({"type": "status", "text": shopify_tool_status_message(name)})
        elif is_knowledge_tool_name(name):
            writer({"type": "status", "text": knowledge_tool_status_message(name)})
        else:
            tool_messages.append(
                ToolMessage(
                    content=json.dumps({"error": "unsupported_tool", "tool": name}),
                    tool_call_id=tc_id,
                    name=name,
                )
            )
            continue
        out = await invoke_shopify_tool_with_timeout(
            bound,
            args,
            tool_name=name,
            conversation_id=conv_uuid,
            round_idx=max(0, model_round - 1),
        )
        body = (out or "").strip()[:120_000] or '{"error": "empty_tool_result"}'
        tool_messages.append(ToolMessage(content=body, tool_call_id=tc_id, name=name))
        invoked.append(name)

    return {
        "messages": tool_messages,
        "tools_invoked": invoked,
        "usage_input_tokens": int(state.get("usage_input_tokens") or 0),
        "usage_output_tokens": int(state.get("usage_output_tokens") or 0),
    }


def build_chat_graph() -> Any:
    graph = StateGraph(ChatGraphState)
    graph.add_node("call_model", _call_model_node)
    graph.add_node("escalation", _escalation_node)
    graph.add_node("shopify_tools", _shopify_tools_node)
    graph.add_edge(START, "call_model")
    graph.add_conditional_edges(
        "call_model",
        _route_after_model,
        {"escalation": "escalation", "shopify_tools": "shopify_tools", "__end__": END},
    )
    graph.add_edge("escalation", END)
    graph.add_conditional_edges(
        "shopify_tools",
        _route_after_shopify_tools,
        {"call_model": "call_model", "__end__": END},
    )
    return graph.compile()


_compiled_graph: Any | None = None


def get_compiled_chat_graph() -> Any:
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_chat_graph()
    return _compiled_graph


def append_escalation_tool_prompt(system_content: str, *, tools_enabled: bool) -> str:
    if not tools_enabled:
        return system_content
    return f"{system_content}\n\n{escalation_tool_system_appendix()}".strip()


async def stream_chat_graph(
    *,
    messages: list[BaseMessage],
    model: str,
    temperature: float,
    fallback_message: str,
    escalation_enabled: bool,
    bound_tools: list[StructuredTool] | None = None,
    turn_context: EscalationTurnContext | None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield ``token``, ``status``, and final ``done`` events."""
    tools = list(bound_tools or [])
    shopify_names = {t.name for t in tools if getattr(t, "name", None) and is_shopify_tool_name(t.name)}

    graph = get_compiled_chat_graph()
    initial: ChatGraphState = {
        "messages": messages,
        "model": model,
        "temperature": temperature,
        "fallback_message": fallback_message,
        "escalation_enabled": escalation_enabled,
        "bound_tools": tools,
        "shopify_tool_names": shopify_names,
        "model_round": 0,
        "tools_invoked": [],
        "escalation_occurred": False,
        "fallback_used": False,
        "usage_input_tokens": 0,
        "usage_output_tokens": 0,
        "turn_context": (
            {
                "user_id": turn_context.user_id,
                "agent_id": turn_context.agent_id,
                "conversation_id": turn_context.conversation_id,
                "user_message": turn_context.user_message,
                "visitor_email": turn_context.visitor_email,
                "esc_cfg": turn_context.esc_cfg,
            }
            if turn_context is not None
            else {}
        ),
    }

    final_state: dict[str, Any] = {}
    try:
        async for mode, chunk in graph.astream(initial, stream_mode=["custom", "updates"]):
            if mode == "custom" and isinstance(chunk, dict):
                if chunk.get("type") in ("token", "status"):
                    yield chunk
            elif mode == "updates" and isinstance(chunk, dict):
                for node_out in chunk.values():
                    if isinstance(node_out, dict):
                        final_state.update(node_out)
    except Exception:
        _log.exception("chat.graph_failed")
        fallback = str(fallback_message or "").strip()
        if fallback:
            final_state.setdefault("final_response", fallback)
            final_state["fallback_used"] = True
            yield {"type": "token", "text": fallback}

    ai = _last_ai_message(final_state.get("messages") or messages)
    answer = str(final_state.get("final_response") or "").strip()
    if not answer and ai is not None:
        answer = text_from_model_message(ai)
    if not answer:
        fallback = str(fallback_message or "").strip()
        if fallback:
            answer = fallback
            yield {"type": "token", "text": fallback}

    yield {
        "type": "done",
        "response": answer,
        "fallback_used": bool(final_state.get("fallback_used")),
        "usage_input_tokens": int(final_state.get("usage_input_tokens") or 0),
        "usage_output_tokens": int(final_state.get("usage_output_tokens") or 0),
        "tools_invoked": list(final_state.get("tools_invoked") or []),
        "escalation_occurred": bool(final_state.get("escalation_occurred")),
    }
