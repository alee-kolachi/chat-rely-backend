from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentReliabilityDTO(BaseModel):
    agent_id: UUID
    user_id: UUID
    min_retrieval_similarity: float
    inactivity_timeout_minutes: int
    max_unresolved_turns_before_escalation: int
    fallback_message: str
    created_at: datetime
    updated_at: datetime


class AgentReliabilityUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_retrieval_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    inactivity_timeout_minutes: int | None = Field(default=None, ge=5, le=240)
    max_unresolved_turns_before_escalation: int | None = Field(default=None, ge=1)
    fallback_message: str | None = Field(default=None, min_length=1, max_length=2000)
