"""SSE chat streaming (dashboard / authenticated)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.agent.service import stream_chat
from app.api.deps import AuthContext, get_current_user, get_db
from app.core.errors import AppError
from app.core.settings import get_settings
from app.domains.agents.rate_limit import (
    enforce_visitor_message_rate_limit,
    fetch_agent_behavior_settings,
)
from app.domains.runtime.schemas import RuntimeChatRequest

log = structlog.get_logger(__name__)
router = APIRouter(tags=["chat"])


@router.post("/stream")
async def chat_stream_route(
    payload: RuntimeChatRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    behavior = await fetch_agent_behavior_settings(db, user_id=user.user_id, agent_id=payload.agent_id)
    visitor_key = (payload.visitor_id or "").strip() or f"dashboard:{user.user_id}"
    await enforce_visitor_message_rate_limit(
        behavior_settings=behavior,
        visitor_id=visitor_key,
        agent_id=payload.agent_id,
    )

    async def generate():
        from app.agent.streaming import format_sse

        try:
            async for frame in stream_chat(user.user_id, payload):
                yield frame.encode("utf-8")
        except AppError as exc:
            yield format_sse(
                "error",
                {"code": exc.code, "message": exc.message, "details": exc.details},
            ).encode("utf-8")
        except Exception:
            log.exception("chat.stream_failed")
            details: dict[str, str] | None = None
            if get_settings().app_env == "development":
                details = {"error": "chat.stream_failed"}
            yield format_sse(
                "error",
                {
                    "code": "chat.stream_failed",
                    "message": "Chat stream failed. Please try again.",
                    **({"details": details} if details else {}),
                },
            ).encode("utf-8")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
