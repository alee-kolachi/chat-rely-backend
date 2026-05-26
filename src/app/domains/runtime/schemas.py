from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

RuntimeChannel = Literal["api", "widget"]

from app.domains.conversation_outcomes.schemas import TurnSignalsDTO


class RuntimeEscalationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    human_escalation_action_enabled: bool
    occurred: bool = False
    seller_live: bool = False
    estimated_minutes: int | None = None
    channel_hint: Literal["live", "email"] | None = None


class RuntimeChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: UUID | None = None
    visitor_id: str = Field(default="preview-user", min_length=1, max_length=255)
    channel: RuntimeChannel = "api"
    system_prompt_override: str | None = None
    agent_type_override: str | None = None
    #: 0–1 — playground preview; when set, overrides agent `behavior_settings.creativity`.
    creativity_override: float | None = Field(default=None, ge=0.0, le=1.0)
    visitor_email: str | None = None
    request_human: bool = False
    #: Browser locale (e.g. en-US); stored on conversation metadata for analytics.
    locale: str | None = None
    #: ISO 3166-1 alpha-2 country from host page or checkout; stored on conversation metadata.
    country_code: str | None = None


class RuntimeChatResponse(BaseModel):
    conversation_id: UUID
    #: When the LLM is suppressed (e.g. human handoff), no assistant row is created this turn.
    assistant_message_id: UUID | None = None
    response: str
    model: str
    fallback_used: bool
    retrieval_count: int
    min_similarity: float
    created_at: datetime
    retrieval_preview: list[dict[str, Any]]
    #: Shopify/LangChain tools bound for this request (0 if no connection or no enabled actions).
    tools_available_count: int = 0
    #: Tool names actually executed this turn, in order (empty if the model never called a tool).
    tools_invoked: list[str] = Field(default_factory=list)
    #: Present when Shopify tools were bound: router LLM classification (None if routing failed or skipped).
    shopify_route_requires_live_data: bool | None = None
    shopify_route_confidence: float | None = None
    #: True when router confidence cleared the threshold and round 0 used tool_choice=required.
    shopify_route_tool_choice_required: bool | None = None
    escalation: RuntimeEscalationInfo | None = None
    #: Present when a lightweight side-classifier ran after the assistant reply.
    turn_signals: TurnSignalsDTO | None = None

