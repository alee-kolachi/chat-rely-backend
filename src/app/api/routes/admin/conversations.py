from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_db, require_admin
from app.domains.admin.conversations_service import (
    get_admin_conversation_detail,
    list_admin_conversations,
)
from app.domains.admin.schemas import (
    AdminConversationDetail,
    AdminConversationListResponse,
)


router = APIRouter()


@router.get("/conversations", response_model=AdminConversationListResponse)
async def list_admin_conversations_route(
    user_id: UUID | None = Query(default=None),
    user_email: str | None = Query(default=None, description="ILIKE match on auth.users.email"),
    agent_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    visitor_id: str | None = Query(default=None),
    started_after: datetime | None = Query(default=None),
    started_before: datetime | None = Query(default=None),
    escalated: bool | None = Query(default=None),
    fallback_used: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminConversationListResponse:
    return await list_admin_conversations(
        db,
        user_id=user_id,
        user_email=user_email,
        agent_id=agent_id,
        status=status,
        channel=channel,
        visitor_id=visitor_id,
        started_after=started_after,
        started_before=started_before,
        escalated=escalated,
        fallback_used=fallback_used,
        page=page,
        page_size=page_size,
    )


@router.get("/conversations/{conversation_id}", response_model=AdminConversationDetail)
async def get_admin_conversation_route(
    conversation_id: UUID,
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminConversationDetail:
    return await get_admin_conversation_detail(db, conversation_id)
