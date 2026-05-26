from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OnboardingStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=120)


class OnboardingWebsiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    website_url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=255)


class OnboardingPreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    model: str | None = Field(default=None, min_length=1, max_length=120)
    tone: str | None = Field(default=None, max_length=120)
    brand_color: str | None = Field(default=None, max_length=40)
    widget_position: str | None = Field(default=None, pattern="^(bottom_right|bottom_left)$")


class OnboardingFinishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID


class OnboardingStepStatusDTO(BaseModel):
    key: str
    status: str
    completed_at: datetime | None
    required: bool


class OnboardingStatusResponse(BaseModel):
    agent_id: UUID
    current_step: int
    progress_pct: int
    session_status: str
    website_url: str | None
    website_title: str | None
    preview_asset: dict[str, Any] | None
    indexing_job: dict[str, Any] | None
    checklist: list[OnboardingStepStatusDTO]


class OnboardingStartResponse(BaseModel):
    agent_id: UUID
    current_step: int


class OnboardingCrawledPageDTO(BaseModel):
    url: str
    path: str
    status: str


class OnboardingWebsiteResponse(BaseModel):
    source_id: UUID
    job_id: UUID
    status: str
    website_url: str
    pages: list[OnboardingCrawledPageDTO] = Field(default_factory=list)
    preview_image_url: str | None = Field(
        default=None,
        description="og:image / twitter:image from the first crawled page when present (many sites block iframes).",
    )
