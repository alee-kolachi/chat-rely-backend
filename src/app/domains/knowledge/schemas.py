from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

KnowledgeSourceType = Literal["website", "file", "text_snippet", "q_and_a"]


class KnowledgeSourceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    type: KnowledgeSourceType
    title: str = Field(min_length=1, max_length=255)
    source_url: str | None = None
    storage_bucket: str | None = None
    storage_path: str | None = None
    raw_text: str | None = Field(default=None, max_length=200_000)
    metadata: dict[str, Any] | None = None
    status: str | None = Field(
        default=None,
        description="When set (e.g. skipped_duplicate), overrides the DB default pending.",
    )


class KnowledgeSourceDTO(BaseModel):
    id: UUID
    agent_id: UUID
    user_id: UUID
    type: str
    title: str
    status: str
    source_url: str | None
    storage_bucket: str | None
    storage_path: str | None
    raw_text: str | None = None
    metadata: dict[str, Any]
    error_message: str | None
    last_indexed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class KnowledgeSourceListResponse(BaseModel):
    sources: list[KnowledgeSourceDTO]


class IndexJobDTO(BaseModel):
    id: UUID
    knowledge_source_id: UUID
    agent_id: UUID
    user_id: UUID
    status: str
    attempt: int
    triggered_by: str
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    phase: str = "queued"
    pages_total: int = 0
    pages_processed: int = 0
    chunks_total: int = 0
    chunks_embedded: int = 0
    progress_pct: int = 0
    metrics: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class IndexJobListResponse(BaseModel):
    jobs: list[IndexJobDTO]


class SourceIndexResponse(BaseModel):
    source: KnowledgeSourceDTO
    job: IndexJobDTO


WebsitePathOperator = Literal["starts_with", "ends_with", "contains", "exact_match", "wildcard"]
WebsiteMode = Literal["crawl", "sitemap", "individual"]


class WebsitePathRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator: WebsitePathOperator
    pattern: str = Field(min_length=1, max_length=2048)


class WebsiteIngestBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    protocol: Literal["https://", "http://"] = "https://"
    url_input: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=255)
    include_rules: list[WebsitePathRule] = Field(default_factory=list)
    exclude_rules: list[WebsitePathRule] = Field(default_factory=list)


class WebsiteCrawlRequest(WebsiteIngestBase):
    pass


class WebsiteSitemapRequest(WebsiteIngestBase):
    pass


class WebsiteUrlPreviewRequest(WebsiteIngestBase):
    """Same shape as crawl/sitemap ingest; used only to estimate filtered URL counts (no DB writes)."""

    max_sample_urls: int = Field(default=30, ge=1, le=100)


class WebsiteUrlPreviewResponse(BaseModel):
    discovery_mode: str
    filtered_url_count: int
    sample_urls: list[str] = Field(default_factory=list)
    truncated: bool = False
    message: str | None = None
    discovery_warning: str | None = Field(
        default=None, description="Non-fatal hint (e.g. nested sitemap walk capped)."
    )
    sitemap_truncated: bool = False


class WebsiteIndividualRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    protocol: Literal["https://", "http://"] = "https://"
    url_input: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=255)


class WebsiteIngestResponse(BaseModel):
    source: KnowledgeSourceDTO
    """Omitted when the source was skipped as a duplicate (no indexing job created)."""
    job: IndexJobDTO | None = None


class WebsiteSourceListItemDTO(BaseModel):
    id: UUID
    agent_id: UUID
    title: str
    source_url: str | None
    status: str
    website_mode: WebsiteMode | None = None
    link_count: int = Field(
        default=0,
        description="Page rows for this source excluding excluded placeholders (includes queued while crawl runs).",
    )
    last_indexed_at: datetime | None = None
    error_message: str | None = None
    latest_job_status: str | None = None
    latest_job_phase: str | None = None
    job_metrics: dict[str, Any] | None = None
    job_pages_total: int | None = None
    job_pages_processed: int | None = None
    job_progress_pct: int | None = None
    job_crawl_limit_exceeded: bool = False
    reindexed_duplicate: bool = False
    duplicate_reason: str | None = None
    duplicate_of_source_id: str | None = None


class WebsiteSourcesListResponse(BaseModel):
    sources: list[WebsiteSourceListItemDTO]


class WebsiteSourcePageItemDTO(BaseModel):
    id: UUID
    url: str
    status: str
    depth: int
    last_indexed_at: datetime | None = None
    http_status: int | None = None


class WebsitePageUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)


class WebsiteSourcePagesResponse(BaseModel):
    pages: list[WebsiteSourcePageItemDTO]
    total: int
    offset: int
    limit: int


class WebsiteUsageResponse(BaseModel):
    plan_slug: str
    plan_name: str
    included_storage_bytes: int
    used_storage_bytes: int
    total_links: int
    total_files: int = 0
    total_snippets: int = 0
    total_qa_pairs: int = 0
    website_used_bytes: int = 0
    files_used_bytes: int = 0
    snippets_used_bytes: int = 0
    qa_used_bytes: int = 0
    show_upgrade: bool
    website_crawl_budget_bytes: int = 0
    website_crawl_last_job_bytes: int | None = None


class KnowledgeWebsiteWorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usage: WebsiteUsageResponse
    sources: list[WebsiteSourceListItemDTO]


class FileSourceListItemDTO(BaseModel):
    id: UUID
    agent_id: UUID
    title: str
    storage_bucket: str | None
    storage_path: str | None
    status: str
    character_count: int = 0
    last_indexed_at: datetime | None = None
    latest_job_status: str | None = None
    latest_job_phase: str | None = None
    job_progress_pct: int | None = None


class FileSourcesListResponse(BaseModel):
    sources: list[FileSourceListItemDTO]


class FileUploadResultDTO(BaseModel):
    source: KnowledgeSourceDTO
    job: IndexJobDTO | None = None
    status: Literal["succeeded", "failed"]
    error_message: str | None = None


class FileUploadResponse(BaseModel):
    results: list[FileUploadResultDTO]


class TextSnippetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    title: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=200_000)


class TextSnippetUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=200_000)


class TextSnippetListItemDTO(BaseModel):
    id: UUID
    agent_id: UUID
    title: str
    status: str
    character_count: int = 0
    preview: str = ""
    last_indexed_at: datetime | None = None
    updated_at: datetime
    latest_job_status: str | None = None
    latest_job_phase: str | None = None
    job_progress_pct: int | None = None


class TextSnippetsListResponse(BaseModel):
    sources: list[TextSnippetListItemDTO]


class TextSnippetDetailDTO(BaseModel):
    id: UUID
    agent_id: UUID
    title: str
    text: str
    status: str
    last_indexed_at: datetime | None = None
    updated_at: datetime


class QAPairCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    question: str = Field(min_length=1, max_length=4_000)
    answer: str = Field(min_length=1, max_length=100_000)


class QAPairUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4_000)
    answer: str = Field(min_length=1, max_length=100_000)


class QAPairListItemDTO(BaseModel):
    id: UUID
    agent_id: UUID
    title: str
    question: str
    answer_preview: str = ""
    character_count: int = 0
    status: str
    last_indexed_at: datetime | None = None
    updated_at: datetime
    latest_job_status: str | None = None
    latest_job_phase: str | None = None
    job_progress_pct: int | None = None


class QAPairsListResponse(BaseModel):
    sources: list[QAPairListItemDTO]


class QAPairDetailDTO(BaseModel):
    id: UUID
    agent_id: UUID
    question: str
    answer: str
    status: str
    last_indexed_at: datetime | None = None
    updated_at: datetime

