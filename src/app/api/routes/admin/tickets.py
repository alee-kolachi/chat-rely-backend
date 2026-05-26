from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_db, require_admin
from app.domains.admin.schemas import AdminTicketDetail, AdminTicketListResponse
from app.domains.admin.tickets_service import (
    SortBy,
    SortDir,
    get_admin_ticket_detail,
    list_admin_tickets,
)


router = APIRouter()


@router.get("/tickets", response_model=AdminTicketListResponse)
async def list_admin_tickets_route(
    user_email: str | None = Query(default=None, description="ILIKE match on auth.users.email"),
    agent_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None, description="open|pending_customer|resolved"),
    priority: str | None = Query(default=None, description="low|medium|high"),
    sort_by: Literal["updated_at", "created_at", "priority", "status"] = Query(default="updated_at"),
    sort_dir: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminTicketListResponse:
    return await list_admin_tickets(
        db,
        user_email=user_email,
        agent_id=agent_id,
        status=status,
        priority=priority,
        sort_by=_cast_sort_by(sort_by),
        sort_dir=_cast_sort_dir(sort_dir),
        page=page,
        page_size=page_size,
    )


@router.get("/tickets/{ticket_id}", response_model=AdminTicketDetail)
async def get_admin_ticket_route(
    ticket_id: UUID,
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminTicketDetail:
    return await get_admin_ticket_detail(db, ticket_id)


def _cast_sort_by(value: str) -> SortBy:
    return value  # type: ignore[return-value]


def _cast_sort_dir(value: str) -> SortDir:
    return value  # type: ignore[return-value]
