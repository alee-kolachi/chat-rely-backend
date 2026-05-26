from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.errors import AppError
from app.domains.message_feedback.service import public_upsert_feedback
from app.domains.plans.plan_limits import message_feedback_enabled_for_plan_slug
from app.domains.plans.subscription_queries import fetch_active_plan_slug
from app.domains.public_widget.schemas import (
    PublicWidgetAgentContext,
    PublicWidgetChatRequest,
    PublicWidgetConfigResponse,
    PublicWidgetMessageFeedbackRequest,
)
from app.domains.public_widget.service import (
    build_public_widget_config_response,
    resolve_agent_for_widget_key,
)
router = APIRouter(prefix="/public/widget", tags=["public-widget"])


async def _widget_agent_context(
    x_chatrely_agent_key: str | None = Header(default=None, alias="X-ChatRely-Agent-Key"),
    db: AsyncSession = Depends(get_db),
) -> PublicWidgetAgentContext:
    if x_chatrely_agent_key is None or not str(x_chatrely_agent_key).strip():
        raise AppError(
            code="widget.missing_key",
            message="Missing X-ChatRely-Agent-Key header",
            status_code=401,
        )
    return await resolve_agent_for_widget_key(db, x_chatrely_agent_key)


WidgetAgentDep = Annotated[PublicWidgetAgentContext, Depends(_widget_agent_context)]


@router.get("/config", response_model=PublicWidgetConfigResponse)
async def public_widget_config_route(
    ctx: WidgetAgentDep,
    db: AsyncSession = Depends(get_db),
) -> PublicWidgetConfigResponse:
    return await build_public_widget_config_response(db, ctx)


@router.post(
    "/message-feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def public_widget_message_feedback_route(
    ctx: WidgetAgentDep,
    payload: PublicWidgetMessageFeedbackRequest,
    db: AsyncSession = Depends(get_db),
) -> Response:
    plan_slug = await fetch_active_plan_slug(db, ctx.user_id)
    if not message_feedback_enabled_for_plan_slug(plan_slug):
        raise AppError(
            code="plan.message_feedback_not_available",
            message="Message feedback is not available for this store.",
            status_code=403,
        )
    await public_upsert_feedback(
        db,
        agent_id=ctx.agent_id,
        message_id=payload.message_id,
        visitor_id=payload.visitor_id,
        value=payload.value,
        remove=payload.remove,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
