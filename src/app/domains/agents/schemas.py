from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    system_prompt: str | None = None
    behavior_settings: dict[str, Any] | None = None


class AgentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    system_prompt: str | None = None
    model: str | None = Field(default=None, min_length=1, max_length=120)
    behavior_settings: dict[str, Any] | None = None
    status: str | None = Field(default=None, pattern="^(active|paused|archived)$")


class AgentDTO(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    slug: str
    public_key: str
    system_prompt: str
    model: str
    behavior_settings: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class AgentListResponse(BaseModel):
    agents: list[AgentDTO]

