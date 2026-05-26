from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user, get_db
from app.domains.tickets.schemas import TicketDetailResponse, TicketListResponse
from app.domains.tickets.service import get_ticket, list_tickets

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("", response_model=TicketListResponse)
async def list_tickets_route(
    agent_id: UUID | None = Query(default=None),
    status: str | None = Query(
        default=None, description="open|pending_customer|resolved"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TicketListResponse:
    items, total = await list_tickets(
        db,
        user_id=user.user_id,
        agent_id=agent_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return TicketListResponse(tickets=items, total=total)


@router.get("/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket_route(
    ticket_id: UUID,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TicketDetailResponse:
    ticket = await get_ticket(db, user.user_id, ticket_id)
    return TicketDetailResponse(ticket=ticket)
