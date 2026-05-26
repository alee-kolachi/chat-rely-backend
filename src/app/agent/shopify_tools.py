"""Shopify tool execution helpers for the chat agent."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import UUID

import structlog
from langchain_core.tools import StructuredTool

from app.core.settings import get_settings
from app.domains.conversations.schemas import MessageDTO
from app.domains.runtime.shopify_lc_tools import tools_by_name

log = structlog.get_logger("agent.shopify_tools")

MAX_SHOPIFY_TOOL_ROUNDS = 5

SHOPIFY_TOOL_STATUS: dict[str, str] = {
    "shopify_product_search": "Searching the catalog…",
    "shopify_order_lookup": "Looking up your order…",
    "shopify_inventory_check": "Checking stock levels…",
    "shopify_customer_context": "Loading your account details…",
}


def shopify_tool_status_message(tool_name: str) -> str:
    return SHOPIFY_TOOL_STATUS.get(tool_name, "Checking store data…")


def is_shopify_tool_name(name: str) -> bool:
    return (name or "").startswith("shopify_")


def thread_had_shopify_tools(history_rows: list[MessageDTO]) -> bool:
    for m in history_rows:
        meta = m.metadata or {}
        for n in meta.get("tools_invoked") or []:
            if is_shopify_tool_name(str(n)):
                return True
        tcp = m.tool_call_payload or {}
        for tc in tcp.get("tool_calls") or []:
            if isinstance(tc, dict) and is_shopify_tool_name(str(tc.get("name") or "")):
                return True
    return False


def tool_call_parts(tc: Any) -> tuple[str, dict[str, Any], str]:
    if isinstance(tc, dict):
        return (
            str(tc.get("name") or ""),
            dict(tc.get("args") or {}),
            str(tc.get("id") or tc.get("name") or "tool"),
        )
    return (
        str(getattr(tc, "name", "") or ""),
        dict(getattr(tc, "args", {}) or {}),
        str(getattr(tc, "id", None) or getattr(tc, "name", None) or "tool"),
    )


async def invoke_shopify_tool_with_timeout(
    tools: list[StructuredTool],
    args: dict[str, Any],
    *,
    tool_name: str,
    conversation_id: UUID | None,
    round_idx: int,
) -> str:
    tool = tools_by_name(tools).get(tool_name)
    timeout_s = float(get_settings().shopify_tool_timeout_seconds)
    if tool is None:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    t0 = time.perf_counter()
    try:
        out = await asyncio.wait_for(tool.ainvoke(args), timeout=timeout_s)
    except TimeoutError:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log.warning(
            "agent.shopify_tool_timeout",
            conversation_id=str(conversation_id) if conversation_id else None,
            tool=tool_name,
            round_idx=round_idx,
            timeout_s=timeout_s,
            elapsed_ms=elapsed_ms,
        )
        return json.dumps(
            {
                "error": "timeout",
                "message": (
                    "The store connection timed out before live data could be loaded. "
                    "Tell the customer to try again shortly."
                ),
            }
        )
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log.warning(
            "agent.shopify_tool_failed",
            conversation_id=str(conversation_id) if conversation_id else None,
            tool=tool_name,
            round_idx=round_idx,
            elapsed_ms=elapsed_ms,
            error=str(exc)[:500],
        )
        return json.dumps(
            {
                "error": "tool_failed",
                "message": (
                    "Live store data could not be loaded right now. "
                    "Tell the customer to try again shortly."
                ),
            }
        )
    if not isinstance(out, str):
        out = str(out)
    return out
