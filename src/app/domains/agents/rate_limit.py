"""Per-visitor message rate limits from agent behavior_settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, RateLimitError
from app.core.rate_limit import build_rate_limit_key, check_rate_keys

DEFAULT_MAX_MESSAGES = 20
DEFAULT_WINDOW_SECONDS = 60
DEFAULT_LIMIT_MESSAGE = "Too many messages. Please try again in a bit."


@dataclass(frozen=True)
class RateLimitPolicy:
    max_messages: int
    window_seconds: int
    limit_message: str


def parse_agent_rate_limit(behavior_settings: dict[str, Any] | None) -> RateLimitPolicy:
    raw = (behavior_settings or {}).get("rate_limit")
    obj = raw if isinstance(raw, dict) else {}
    max_messages = _positive_int(obj.get("max_messages"), DEFAULT_MAX_MESSAGES)
    window_seconds = _positive_int(obj.get("window_seconds"), DEFAULT_WINDOW_SECONDS)
    msg = obj.get("limit_message")
    limit_message = msg.strip() if isinstance(msg, str) and msg.strip() else DEFAULT_LIMIT_MESSAGE
    return RateLimitPolicy(
        max_messages=max_messages,
        window_seconds=window_seconds,
        limit_message=limit_message,
    )


def _positive_int(value: Any, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


async def enforce_visitor_message_rate_limit(
    *,
    behavior_settings: dict[str, Any] | None,
    visitor_id: str,
    agent_id: UUID,
) -> None:
    policy = parse_agent_rate_limit(behavior_settings)
    key = build_rate_limit_key(
        scope="visitor_msg",
        tier=str(agent_id),
        identifier=visitor_id.strip() or "anonymous",
    )
    allowed, retry_after = await check_rate_keys(
        [key],
        limit=policy.max_messages,
        window_seconds=policy.window_seconds,
    )
    if not allowed:
        raise RateLimitError(
            policy.limit_message,
            retry_after_seconds=retry_after,
        )


async def fetch_agent_behavior_settings(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
) -> dict[str, Any]:
    result = await db.execute(
        text(
            """
            select behavior_settings
            from public.agents
            where id = :agent_id and user_id = :user_id
            """
        ),
        {"agent_id": str(agent_id), "user_id": str(user_id)},
    )
    row = result.mappings().first()
    if row is None:
        raise AppError(code="agent.not_found", message="Agent not found", status_code=404)
    behavior = row["behavior_settings"]
    return behavior if isinstance(behavior, dict) else {}
