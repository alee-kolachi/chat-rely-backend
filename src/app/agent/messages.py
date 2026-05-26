"""Message helpers for chat turns."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.domains.conversations.schemas import MessageDTO


def text_delta_from_stream_chunk(chunk: Any) -> str:
    raw = getattr(chunk, "content", None)
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for block in raw:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def text_from_model_message(msg: BaseMessage) -> str:
    raw = getattr(msg, "content", None)
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        parts: list[str] = []
        for block in raw:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts).strip()
    return ""


def usage_tokens_from_model_message(msg: BaseMessage) -> tuple[int, int]:
    usage = getattr(msg, "usage_metadata", None)
    if isinstance(usage, dict):
        return (
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
        )
    response_meta = getattr(msg, "response_metadata", None)
    if isinstance(response_meta, dict):
        token_usage = response_meta.get("token_usage")
        if isinstance(token_usage, dict):
            return (
                int(token_usage.get("prompt_tokens") or 0),
                int(token_usage.get("completion_tokens") or 0),
            )
    return (0, 0)


def db_messages_to_chat_messages(messages: list[MessageDTO]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in messages:
        if m.role == "user":
            out.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            tcp = m.tool_call_payload or {}
            tcalls = tcp.get("tool_calls")
            if tcalls and isinstance(tcalls, list):
                out.append(AIMessage(content=m.content or "", tool_calls=tcalls))
            else:
                out.append(AIMessage(content=m.content))
        elif m.role == "system":
            continue
        elif m.role == "tool":
            out.append(
                ToolMessage(
                    content=m.content,
                    tool_call_id=m.tool_call_id or m.tool_name or "tool",
                )
            )
    return out


def build_turn_messages(
    *,
    system_content: str,
    history_without_current_user: list[MessageDTO],
    grounded_user_content: str,
) -> list[BaseMessage]:
    messages: list[BaseMessage] = [SystemMessage(content=system_content)]
    messages.extend(db_messages_to_chat_messages(history_without_current_user))
    messages.append(HumanMessage(content=grounded_user_content))
    return messages


def slice_history_for_current_turn(
    history_rows: list[MessageDTO],
    *,
    current_user_content: str,
    max_window_messages: int,
) -> list[MessageDTO]:
    """Drop duplicate trailing user row and cap window size."""
    rows = list(history_rows)
    if rows and rows[-1].role == "user" and (rows[-1].content or "").strip() == current_user_content.strip():
        rows = rows[:-1]
    cap = max(2, int(max_window_messages))
    if len(rows) <= cap:
        return rows
    return rows[-cap:]
