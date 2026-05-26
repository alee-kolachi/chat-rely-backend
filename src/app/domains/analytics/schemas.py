from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domains.message_feedback.schemas import MessageFeedbackAnalyticsDTO


class AnalyticsSeriesPoint(BaseModel):
    model_config = {"extra": "forbid"}

    bucket_date: date
    count: int


class AnalyticsNamedCount(BaseModel):
    model_config = {"extra": "forbid"}

    key: str
    label: str
    count: int


class AnalyticsSentimentSlice(BaseModel):
    model_config = {"extra": "forbid"}

    bucket: str
    count: int
    pct: float | None = None


class AnalyticsQualityMetric(BaseModel):
    model_config = {"extra": "forbid"}

    key: str
    label: str
    value: str
    hint: str


class AgentAnalyticsResponse(BaseModel):
    model_config = {"extra": "forbid"}

    analytics_tier: Literal["basic", "full"]
    range_from: datetime
    range_to: datetime
    conversations_started: int
    resolved_by_agent_pct: float | None = None
    escalations_pct: float | None = None
    avg_response_time_ms: float | None = None
    series: list[AnalyticsSeriesPoint] = Field(default_factory=list)
    top_intents: list[AnalyticsNamedCount] = Field(default_factory=list)
    sentiment: list[AnalyticsSentimentSlice] = Field(default_factory=list)
    countries: list[AnalyticsNamedCount] = Field(default_factory=list)
    quality: list[AnalyticsQualityMetric] = Field(default_factory=list)
    message_feedback: MessageFeedbackAnalyticsDTO | None = None
