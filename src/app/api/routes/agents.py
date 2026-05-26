from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user, get_db
from app.core.errors import AppError
from app.domains.agents.reliability_schemas import (
    AgentReliabilityDTO,
    AgentReliabilityUpdateRequest,
)
from app.domains.agents.reliability_service import get_reliability, update_reliability
from app.domains.agents.schemas import (
    AgentCreateRequest,
    AgentDTO,
    AgentListResponse,
    AgentUpdateRequest,
)
from app.domains.agents.service import create_agent, list_agents, update_agent
from app.domains.analytics.schemas import AgentAnalyticsResponse
from app.domains.analytics.service import build_agent_analytics
from app.domains.dashboard.schemas import AgentDashboardResponse
from app.domains.dashboard.service import build_agent_dashboard, resolve_dashboard_range
from app.domains.message_feedback.schemas import (
    MessageFeedbackAnalyticsDTO,
    MessageFeedbackResolveRequest,
    MessageFeedbackVoteRequest,
)
from app.domains.message_feedback.service import (
    build_message_feedback_analytics,
    owner_upsert_feedback,
    resolve_message_feedback,
)
from app.domains.plans.plan_limits import (
    analytics_access_tier_for_plan_slug,
    message_feedback_enabled_for_plan_slug,
)
from app.domains.plans.subscription_queries import fetch_active_plan_slug

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", response_model=AgentDTO)
async def create_agent_route(
    payload: AgentCreateRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentDTO:
    return await create_agent(db, user.user_id, payload)


@router.get("", response_model=AgentListResponse)
async def list_agents_route(
    include_archived: bool = Query(default=False),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentListResponse:
    agents = await list_agents(db, user.user_id, include_archived=include_archived)
    return AgentListResponse(agents=agents)


@router.patch("/{agent_id}", response_model=AgentDTO)
async def update_agent_route(
    agent_id: UUID,
    payload: AgentUpdateRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentDTO:
    return await update_agent(db, user.user_id, agent_id, payload)


@router.get("/{agent_id}/dashboard", response_model=AgentDashboardResponse)
async def get_agent_dashboard_route(
    agent_id: UUID,
    range_key: str | None = Query(
        default=None,
        description="7d, 30d, 90d, 365d (ignored if from/to set)",
    ),
    range_from: datetime | None = Query(default=None, alias="from"),
    range_to: datetime | None = Query(default=None, alias="to"),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentDashboardResponse:
    return await build_agent_dashboard(
        db,
        user_id=user.user_id,
        agent_id=agent_id,
        range_key=range_key,
        range_from=range_from,
        range_to=range_to,
        tick_lifecycle=False,
    )


@router.get("/{agent_id}/reliability", response_model=AgentReliabilityDTO)
async def get_agent_reliability_route(
    agent_id: UUID,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentReliabilityDTO:
    return await get_reliability(db, user.user_id, agent_id)


@router.patch("/{agent_id}/reliability", response_model=AgentReliabilityDTO)
async def update_agent_reliability_route(
    agent_id: UUID,
    payload: AgentReliabilityUpdateRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentReliabilityDTO:
    return await update_reliability(db, user.user_id, agent_id, payload)


@router.get("/{agent_id}/analytics/message-feedback", response_model=MessageFeedbackAnalyticsDTO)
async def get_agent_message_feedback_analytics_route(
    agent_id: UUID,
    range_key: str | None = Query(
        default=None,
        description="7d, 30d, 90d, 365d (ignored if from/to set)",
    ),
    range_from: datetime | None = Query(default=None, alias="from"),
    range_to: datetime | None = Query(default=None, alias="to"),
    include_playground: bool = Query(
        default=False,
        description="Include preview/playground visitor threads in message feedback aggregates.",
    ),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageFeedbackAnalyticsDTO:
    plan_slug = await fetch_active_plan_slug(db, user.user_id)
    tier = analytics_access_tier_for_plan_slug(plan_slug)
    if tier == "none":
        raise AppError(
            code="plan.analytics_not_available",
            message="Analytics is not available on your plan.",
            status_code=403,
        )
    if not message_feedback_enabled_for_plan_slug(plan_slug):
        raise AppError(
            code="plan.message_feedback_not_available",
            message="Message feedback is not available on your plan.",
            status_code=403,
        )
    rf, rt = resolve_dashboard_range(
        range_key=range_key, range_from=range_from, range_to=range_to
    )
    return await build_message_feedback_analytics(
        db,
        user_id=user.user_id,
        agent_id=agent_id,
        range_from=rf,
        range_to=rt,
        include_playground=include_playground,
    )


@router.get("/{agent_id}/analytics", response_model=AgentAnalyticsResponse)
async def get_agent_analytics_route(
    agent_id: UUID,
    range_key: str | None = Query(
        default=None,
        description="7d, 30d, 90d, 365d (ignored if from/to set)",
    ),
    range_from: datetime | None = Query(default=None, alias="from"),
    range_to: datetime | None = Query(default=None, alias="to"),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentAnalyticsResponse:
    plan_slug = await fetch_active_plan_slug(db, user.user_id)
    tier = analytics_access_tier_for_plan_slug(plan_slug)
    if tier == "none":
        raise AppError(
            code="plan.analytics_not_available",
            message="Analytics is not available on your plan.",
            status_code=403,
        )
    base = await build_agent_analytics(
        db,
        user_id=user.user_id,
        agent_id=agent_id,
        analytics_tier=tier,
        range_key=range_key,
        range_from=range_from,
        range_to=range_to,
        tick_lifecycle=False,
    )
    if message_feedback_enabled_for_plan_slug(plan_slug):
        mf = await build_message_feedback_analytics(
            db,
            user_id=user.user_id,
            agent_id=agent_id,
            range_from=base.range_from,
            range_to=base.range_to,
            include_playground=False,
        )
        return base.model_copy(update={"message_feedback": mf})
    return base


@router.post(
    "/{agent_id}/message-feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def post_agent_message_feedback_route(
    agent_id: UUID,
    payload: MessageFeedbackVoteRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    plan_slug = await fetch_active_plan_slug(db, user.user_id)
    if not message_feedback_enabled_for_plan_slug(plan_slug):
        raise AppError(
            code="plan.message_feedback_not_available",
            message="Message feedback is not available on your plan.",
            status_code=403,
        )
    await owner_upsert_feedback(
        db,
        user_id=user.user_id,
        agent_id=agent_id,
        message_id=payload.message_id,
        value=payload.value,
        visitor_id=payload.visitor_id,
        remove=payload.remove,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{agent_id}/message-feedback/resolve",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def post_agent_message_feedback_resolve_route(
    agent_id: UUID,
    payload: MessageFeedbackResolveRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    plan_slug = await fetch_active_plan_slug(db, user.user_id)
    if not message_feedback_enabled_for_plan_slug(plan_slug):
        raise AppError(
            code="plan.message_feedback_not_available",
            message="Message feedback is not available on your plan.",
            status_code=403,
        )
    await resolve_message_feedback(
        db,
        user_id=user.user_id,
        agent_id=agent_id,
        message_id=payload.message_id,
        resolved=payload.resolved,
        note=payload.note,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

