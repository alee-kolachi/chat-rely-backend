from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DashboardSeriesPoint(BaseModel):
    model_config = {"extra": "forbid"}

    bucket_date: date
    count: int


class DashboardRecentRow(BaseModel):
    model_config = {"extra": "forbid"}

    conversation_id: UUID
    visitor_id: str
    topic_preview: str | None
    status: str
    last_activity_at: datetime


class TrainingTopicSummary(BaseModel):
    model_config = {"extra": "forbid"}

    slug: str
    label: str
    count: int


class AgentDashboardResponse(BaseModel):
    model_config = {"extra": "forbid"}

    range_from: datetime
    range_to: datetime
    conversations_started: int
    active_conversations: int = 0
    resolved_by_agent_pct: float | None = None
    needs_human_pct: float | None = None
    open_escalations: int = 0
    awaiting_customer_reply: int = 0
    series: list[DashboardSeriesPoint] = Field(default_factory=list)
    recent: list[DashboardRecentRow] = Field(default_factory=list)
    training_topics: list[TrainingTopicSummary] = Field(default_factory=list)
    sources_suggestions_enabled: bool = False
