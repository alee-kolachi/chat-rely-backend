from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

OutcomeEndReason = Literal[
    "satisfied_close",
    "user_abandoned",
    "window_closed",
    "frustration_exit",
    "abuse_or_policy",
    "escalated_to_human",
    "insufficient_knowledge",
    "other",
]


class TrainingTopicItem(BaseModel):
    model_config = {"extra": "forbid"}

    slug: str
    label: str


class ConversationOutcomePayload(BaseModel):
    """Persisted JSON payload for a closed conversation (dashboard + drill-down)."""

    model_config = {"extra": "allow"}

    resolved_by_agent: bool = False
    resolution_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    end_reason: OutcomeEndReason = "other"
    evidence: str = ""
    training_topics: list[TrainingTopicItem] = Field(default_factory=list)
    needs_follow_up_training: bool = False
    #: Canonical label for the customer's main goal (catalog match or new intent from closure LLM).
    primary_intent: str = ""
    #: Stable slug: either an existing catalog slug or slugified new_intent_label.
    primary_intent_slug: str = ""


class ConversationOutcomeDTO(BaseModel):
    id: UUID
    conversation_id: UUID
    agent_id: UUID
    user_id: UUID
    computed_at: datetime
    model: str
    payload: ConversationOutcomePayload
    created_at: datetime
    updated_at: datetime


class ConversationOutcomeLLMResult(BaseModel):
    """Raw structured output from the closure LLM (before slug enrichment)."""

    model_config = {"extra": "forbid"}

    resolved_by_agent: bool
    resolution_confidence: float = Field(ge=0.0, le=1.0)
    end_reason: OutcomeEndReason
    evidence: str = ""
    training_topics: list[str] = Field(default_factory=list)
    needs_follow_up_training: bool = False
    #: Must be an exact slug from the intent catalog passed in the prompt, or null.
    matched_intent_slug: str | None = None
    #: When no catalog entry fits, a short canonical label for a new intent (catalog was empty or no match).
    new_intent_label: str | None = None


class TurnSignals(BaseModel):
    """Lightweight per-turn hints merged into closure analysis input."""

    model_config = {"extra": "forbid"}

    likely_resolved: bool = False
    customer_sentiment: Literal["neutral", "positive", "negative", "frustrated"] = "neutral"
    knowledge_gap: bool = False


class TurnSignalsDTO(BaseModel):
    likely_resolved: bool = False
    customer_sentiment: Literal["neutral", "positive", "negative", "frustrated"] = "neutral"
    knowledge_gap: bool = False


def slugify_topic_label(label: str) -> str:
    import re

    s = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")
    return s or "topic"
