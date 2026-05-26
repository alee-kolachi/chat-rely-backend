from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ConversationSummaryLLMResult(BaseModel):
    model_config = {"extra": "forbid"}

    summary: str = Field(default="", max_length=2500)
    key_points: list[str] = Field(default_factory=list)


class ConversationSummaryDTO(BaseModel):
    conversation_id: UUID
    summary: str
    key_points: list[str] = Field(default_factory=list)
    computed_at: datetime
    message_count: int
    stale: bool = False
    model: str = ""


class ConversationSummaryStateResponse(BaseModel):
    """GET: cached summary if present (does not generate)."""

    conversation_id: UUID
    summary: str | None = None
    key_points: list[str] = Field(default_factory=list)
    computed_at: datetime | None = None
    message_count: int = 0
    stale: bool = False
    model: str = ""


class ConversationSummaryGenerateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    regenerate: bool = False
