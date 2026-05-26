import asyncio

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user, get_db
from app.db.session import get_session_factory
from app.domains.notifications.schemas import (
    MarkNotificationsReadRequest,
    MarkNotificationsReadResponse,
    NotificationListResponse,
)
from app.domains.notifications.service import list_notifications, mark_notifications_read

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse)
async def list_notifications_route(
    limit: int = Query(default=50, ge=1, le=100),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationListResponse:
    items, unread = await list_notifications(db, user_id=user.user_id, limit=limit)
    return NotificationListResponse(notifications=items, unread_count=unread)


@router.get("/stream")
async def notifications_stream_route(
    limit: int = Query(default=50, ge=1, le=100),
    user: AuthContext = Depends(get_current_user),
) -> StreamingResponse:
    """SSE stream with periodic snapshots (same shape as GET /notifications)."""

    async def event_gen():
        sf = get_session_factory()
        while True:
            async with sf() as db:
                items, unread = await list_notifications(db, user_id=user.user_id, limit=limit)
            payload = NotificationListResponse(notifications=items, unread_count=unread)
            yield f"data: {payload.model_dump_json()}\n\n"
            await asyncio.sleep(25)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.post("/read", response_model=MarkNotificationsReadResponse)
async def mark_notifications_read_route(
    payload: MarkNotificationsReadRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MarkNotificationsReadResponse:
    n = await mark_notifications_read(
        db,
        user_id=user.user_id,
        notification_ids=payload.notification_ids,
        mark_all=payload.mark_all,
    )
    return MarkNotificationsReadResponse(updated=n)
