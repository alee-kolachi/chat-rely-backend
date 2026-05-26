"""SSE chat streaming for the embed widget."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.agent.service import stream_chat
from app.api.deps import get_db
from app.core.errors import AppError
from app.core.settings import get_settings
from app.domains.agents.rate_limit import enforce_visitor_message_rate_limit
from app.domains.public_widget.schemas import PublicWidgetAgentContext, PublicWidgetChatRequest
from app.domains.public_widget.service import resolve_agent_for_widget_key
from app.domains.runtime.schemas import RuntimeChatRequest

router = APIRouter(prefix="/public", tags=["chat-public"])


async def _widget_agent_context(
    x_chatrely_agent_key: str | None = Header(default=None, alias="X-ChatRely-Agent-Key"),
    db: AsyncSession = Depends(get_db),
):
    if x_chatrely_agent_key is None or not str(x_chatrely_agent_key).strip():
        raise AppError(
            code="widget.missing_key",
            message="Missing X-ChatRely-Agent-Key header",
            status_code=401,
        )
    return await resolve_agent_for_widget_key(db, x_chatrely_agent_key)


WidgetAgentDep = Annotated[PublicWidgetAgentContext, Depends(_widget_agent_context)]


@router.post("/stream")
async def chat_public_stream_route(
    ctx: WidgetAgentDep,
    payload: PublicWidgetChatRequest,
) -> StreamingResponse:
    await enforce_visitor_message_rate_limit(
        behavior_settings=ctx.behavior_settings,
        visitor_id=payload.visitor_id,
        agent_id=ctx.agent_id,
    )

    runtime_payload = RuntimeChatRequest(
        agent_id=ctx.agent_id,
        message=payload.message,
        conversation_id=payload.conversation_id,
        visitor_id=payload.visitor_id,
        visitor_email=payload.visitor_email,
        request_human=payload.request_human,
        locale=payload.locale,
        country_code=payload.country_code,
        channel="widget",
    )

    async def generate():
        from app.agent.streaming import format_sse

        try:
            async for frame in stream_chat(ctx.user_id, runtime_payload):
                yield frame.encode("utf-8")
        except AppError as exc:
            yield format_sse(
                "error",
                {"code": exc.code, "message": exc.message, "details": exc.details},
            ).encode("utf-8")
        except Exception:
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
