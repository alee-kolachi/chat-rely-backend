import asyncio
import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user, get_db
from app.db.session import get_session_factory
from app.core.errors import AppError
from app.domains.conversation_outcomes.service import analyze_and_persist_outcome
from app.domains.conversations.schemas import (
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationMessageCreateRequest,
    ConversationUpdateRequest,
    ConversationWorkspaceResponse,
    MessageDTO,
)
from app.domains.conversation_summaries.schemas import (
    ConversationSummaryDTO,
    ConversationSummaryGenerateRequest,
    ConversationSummaryStateResponse,
)
from app.domains.conversation_summaries.service import (
    generate_conversation_summary,
    get_conversation_summary_state,
)
from app.domains.conversations.service import (
    append_message,
    get_conversation,
    list_conversations,
    list_messages,
    mark_conversation_operator_engaged,
    update_conversation_status,
)
from app.domains.integrations.mailjet.notify import maybe_send_ticket_email_reply

router = APIRouter(prefix="/conversations", tags=["conversations"])
log = logging.getLogger(__name__)


async def _analyze_outcome_in_background(user_id: UUID, conversation_id: UUID) -> None:
    async with get_session_factory()() as db:
        try:
            await analyze_and_persist_outcome(db, user_id=user_id, conversation_id=conversation_id)
        except Exception as exc:
            log.warning(
                "outcome.background_failed",
                conversation_id=str(conversation_id),
                error=str(exc),
            )


async def _conversation_workspace_response(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID | None,
    status: str | None,
    started_after: datetime | None,
    started_before: datetime | None,
    training_topic: str | None,
    limit: int,
    offset: int,
    detail_conversation_id: UUID | None,
) -> ConversationWorkspaceResponse:
    conversations = await list_conversations(
        db,
        user_id,
        agent_id=agent_id,
        status=status,
        started_after=started_after,
        started_before=started_before,
        training_topic_slug=training_topic,
        limit=limit,
        offset=offset,
    )
    detail: ConversationDetailResponse | None = None
    if detail_conversation_id is not None:
        try:
            conversation = await get_conversation(db, user_id, detail_conversation_id)
            messages = await list_messages(db, user_id, detail_conversation_id)
            detail = ConversationDetailResponse(conversation=conversation, messages=messages)
        except AppError as exc:
            if exc.status_code != 404:
                raise
    return ConversationWorkspaceResponse(conversations=conversations, detail=detail)


@router.get("", response_model=ConversationListResponse)
async def list_conversations_route(
    agent_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    started_after: datetime | None = Query(default=None),
    started_before: datetime | None = Query(default=None),
    training_topic: str | None = Query(
        default=None, description="Filter by training topic slug from conversation outcomes"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationListResponse:
    conversations = await list_conversations(
        db,
        user.user_id,
        agent_id=agent_id,
        status=status,
        started_after=started_after,
        started_before=started_before,
        training_topic_slug=training_topic,
        limit=limit,
        offset=offset,
    )
    return ConversationListResponse(conversations=conversations)


@router.get("/workspace", response_model=ConversationWorkspaceResponse)
async def conversations_workspace_route(
    agent_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    started_after: datetime | None = Query(default=None),
    started_before: datetime | None = Query(default=None),
    training_topic: str | None = Query(
        default=None, description="Filter by training topic slug from conversation outcomes"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    detail_conversation_id: UUID | None = Query(default=None),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationWorkspaceResponse:
    """List conversations and optionally hydrate one thread in a single round-trip."""
    return await _conversation_workspace_response(
        db,
        user_id=user.user_id,
        agent_id=agent_id,
        status=status,
        started_after=started_after,
        started_before=started_before,
        training_topic=training_topic,
        limit=limit,
        offset=offset,
        detail_conversation_id=detail_conversation_id,
    )


@router.get("/workspace/stream")
async def conversations_workspace_stream_route(
    agent_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    started_after: datetime | None = Query(default=None),
    started_before: datetime | None = Query(default=None),
    training_topic: str | None = Query(
        default=None, description="Filter by training topic slug from conversation outcomes"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    detail_conversation_id: UUID | None = Query(default=None),
    user: AuthContext = Depends(get_current_user),
) -> StreamingResponse:
    """Long-lived SSE connection: pushes the same payload as GET /workspace on an interval (replaces tight polling)."""

    async def event_gen():
        sf = get_session_factory()
        while True:
            async with sf() as db:
                data = await _conversation_workspace_response(
                    db,
                    user_id=user.user_id,
                    agent_id=agent_id,
                    status=status,
                    started_after=started_after,
                    started_before=started_before,
                    training_topic=training_topic,
                    limit=limit,
                    offset=offset,
                    detail_conversation_id=detail_conversation_id,
                )
            yield f"data: {data.model_dump_json()}\n\n"
            await asyncio.sleep(8)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation_route(
    conversation_id: UUID,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationDetailResponse:
    conversation = await get_conversation(db, user.user_id, conversation_id)
    messages = await list_messages(db, user.user_id, conversation_id)
    return ConversationDetailResponse(conversation=conversation, messages=messages)


@router.get("/{conversation_id}/summary", response_model=ConversationSummaryStateResponse)
async def get_conversation_summary_route(
    conversation_id: UUID,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationSummaryStateResponse:
    return await get_conversation_summary_state(db, user.user_id, conversation_id)


@router.post("/{conversation_id}/summary", response_model=ConversationSummaryDTO)
async def generate_conversation_summary_route(
    conversation_id: UUID,
    payload: ConversationSummaryGenerateRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationSummaryDTO:
    return await generate_conversation_summary(
        db,
        user.user_id,
        conversation_id,
        regenerate=payload.regenerate,
    )


@router.post("/{conversation_id}/messages", response_model=MessageDTO)
async def append_message_route(
    conversation_id: UUID,
    payload: ConversationMessageCreateRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageDTO:
    msg = await append_message(db, user.user_id, conversation_id, payload)
    if payload.role == "assistant" and (payload.content or "").strip():
        await mark_conversation_operator_engaged(db, user.user_id, conversation_id)
        await maybe_send_ticket_email_reply(
            db,
            user_id=user.user_id,
            conversation_id=conversation_id,
            assistant_content=payload.content.strip(),
        )
    return msg


@router.patch("/{conversation_id}", response_model=ConversationDetailResponse)
async def update_conversation_route(
    conversation_id: UUID,
    payload: ConversationUpdateRequest,
    background_tasks: BackgroundTasks,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationDetailResponse:
    conversation = await update_conversation_status(db, user.user_id, conversation_id, payload)
    if payload.status in ("idle_closed", "resolved", "escalated"):
        background_tasks.add_task(_analyze_outcome_in_background, user.user_id, conversation_id)
    messages = await list_messages(db, user.user_id, conversation_id)
    return ConversationDetailResponse(conversation=conversation, messages=messages)

