from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationDTO(BaseModel):
    id: UUID
    kind: str
    title: str
    body: str
    href: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    read_at: datetime | None
    created_at: datetime


class NotificationListResponse(BaseModel):
    notifications: list[NotificationDTO]
    unread_count: int


class MarkNotificationsReadRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    notification_ids: list[UUID] | None = None
    mark_all: bool = Field(default=False, alias="all")


class MarkNotificationsReadResponse(BaseModel):
    updated: int
