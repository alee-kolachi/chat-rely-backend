"""Admin-panel DTOs (Phase 2: users + conversations, Phase 3: costing).

These shapes are surfaced under `/api/v1/admin/*` and are gated by `require_admin`.
Read-only views — no mutating routes use these schemas.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# Money policy: USD floats throughout. Per-message costs go down to fractions of a cent
# ($0.0001 territory) so cents-int (used by `subscriptions.monthly_price_cents`) loses
# precision. Display rounding lives in `app/lib/admin/cost-format.ts`.


# ---------- Users ----------------------------------------------------------------


class AdminUserListItem(BaseModel):
    id: UUID
    email: str
    full_name: str | None = None
    signed_up_at: datetime
    plan_slug: str | None = None
    plan_name: str | None = None
    subscription_status: str | None = None
    agents_count: int = 0
    conversations_mtd: int = 0
    last_activity_at: datetime | None = None
    # Phase 3 costing (calendar month-to-date, UTC).
    revenue_mtd_usd: float = 0.0
    llm_cost_mtd_usd: float = 0.0
    embedding_cost_mtd_usd: float = 0.0
    total_cost_mtd_usd: float = 0.0
    margin_mtd_usd: float = 0.0
    margin_pct_mtd: float | None = None  # None when revenue_mtd_usd == 0


class AdminUserListResponse(BaseModel):
    items: list[AdminUserListItem]
    total: int
    page: int
    page_size: int


class AdminSubscriptionSummary(BaseModel):
    id: UUID
    plan_slug: str
    plan_name: str
    monthly_price_cents: int
    status: str
    provider: str
    provider_customer_id: str | None = None
    provider_subscription_id: str | None = None
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    created_at: datetime


class AdminAgentSummary(BaseModel):
    id: UUID
    name: str
    slug: str
    model: str
    status: str
    conversations_total: int = 0
    conversations_mtd: int = 0
    created_at: datetime
    archived_at: datetime | None = None


class AdminKnowledgeSummary(BaseModel):
    """Per-user roll-up of knowledge sources + chunks across all their agents."""

    by_kind: dict[str, int] = Field(default_factory=dict)
    total_sources: int = 0
    total_chunks: int = 0


class AdminUsageSnapshotSummary(BaseModel):
    id: UUID
    period_start: date
    period_end: date
    included_conversations: int
    conversations_used: int
    overage_conversations: int
    estimated_overage_cents: int
    projected_conversations: int
    throttle_tier: str
    included_premium_turns: int = 0
    premium_turns_used: int = 0
    last_computed_at: datetime | None = None


# Forward declaration: AdminUserDetail references AdminConversationListItem (declared below).
# Pydantic resolves with `model_rebuild()` at module-bottom.


# ---------- Conversations --------------------------------------------------------


class AdminConversationListItem(BaseModel):
    id: UUID
    started_at: datetime
    last_activity_at: datetime
    status: str
    channel: str
    visitor_id: str
    agent_id: UUID
    agent_name: str
    user_id: UUID
    user_email: str
    customer_message_count: int = 0
    assistant_message_count: int = 0
    tool_call_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    fallback_used: bool = False
    latest_message_preview: str | None = None
    # Phase 3 costing: total LLM cost across all messages in this conversation.
    # None when none of the conversation's models are priced in env.
    cost_usd: float | None = None


class AdminConversationListResponse(BaseModel):
    items: list[AdminConversationListItem]
    total: int
    page: int
    page_size: int


class AdminMessageDTO(BaseModel):
    id: UUID
    role: str
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_call_payload: dict[str, Any] = Field(default_factory=dict)
    tool_result_payload: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    # Phase 3 costing — None for non-LLM messages (user/system/tool with no model)
    # or for assistant turns whose model isn't in the env price map.
    cost_usd: float | None = None


class AdminConversationDetail(AdminConversationListItem):
    closed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    messages: list[AdminMessageDTO] = Field(default_factory=list)
    truncated: bool = False
    """True when the transcript was capped at the message limit (1000 by default)."""
    total_message_count: int = 0
    transcript_message_cap: int = 1000


# ---------- User detail (declared after conversation list item) ------------------


class AdminUserDetail(BaseModel):
    id: UUID
    email: str
    full_name: str | None = None
    avatar_url: str | None = None
    timezone: str = "UTC"
    signed_up_at: datetime
    subscriptions: list[AdminSubscriptionSummary] = Field(default_factory=list)
    agents: list[AdminAgentSummary] = Field(default_factory=list)
    recent_conversations: list[AdminConversationListItem] = Field(default_factory=list)
    knowledge_summary: AdminKnowledgeSummary = Field(default_factory=AdminKnowledgeSummary)
    recent_usage_snapshots: list[AdminUsageSnapshotSummary] = Field(default_factory=list)
    # Phase 3 costing snapshot — same numbers shown on /admin/users for this user (MTD).
    revenue_mtd_usd: float = 0.0
    llm_cost_mtd_usd: float = 0.0
    embedding_cost_mtd_usd: float = 0.0
    total_cost_mtd_usd: float = 0.0
    margin_mtd_usd: float = 0.0
    margin_pct_mtd: float | None = None


# ---------- Phase 3: Costing -----------------------------------------------------


class AdminCostEventRow(BaseModel):
    """One row from ``conversation_cost_events`` (true-cost ledger)."""

    id: UUID
    kind: str
    provider_model: str | None = None
    turn_user_message_id: UUID | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_tokens: int = 0
    cost_usd: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AdminCostKindRollup(BaseModel):
    kind: str
    cost_usd: float = 0.0
    count: int = 0


class AdminCostPerTurnRollup(BaseModel):
    turn_user_message_id: UUID
    cost_usd: float = 0.0
    event_count: int = 0


class AdminCostByModelRow(BaseModel):
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_tokens: int = 0
    cost_usd: float = 0.0
    pct_of_total: float = 0.0  # 0..100


class AdminCostByAgentRow(BaseModel):
    agent_id: UUID
    agent_name: str
    llm_cost_usd: float = 0.0
    embedding_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    conversations: int = 0
    messages: int = 0


class AdminMessageCostRow(BaseModel):
    """Lightweight per-message cost row used by /conversations/{id}/cost."""

    id: UUID
    role: str
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    created_at: datetime


class AdminConversationCost(BaseModel):
    conversation_id: UUID
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float | None = None  # None when no priced messages exist
    by_model: list[AdminCostByModelRow] = Field(default_factory=list)
    messages: list[AdminMessageCostRow] = Field(default_factory=list)
    has_unknown_models: bool = False
    # True-cost ledger (forward from migration); empty for older conversations.
    cost_events: list[AdminCostEventRow] = Field(default_factory=list)
    events_total_cost_usd: float | None = None
    has_unknown_event_pricing: bool = False
    by_kind: list[AdminCostKindRollup] = Field(default_factory=list)
    per_turn: list[AdminCostPerTurnRollup] = Field(default_factory=list)
    customer_message_count: int = 0
    avg_cost_per_customer_message_usd: float | None = None


class AdminUserCosting(BaseModel):
    user_id: UUID
    email: str
    period_label: str = "MTD"
    revenue_usd: float = 0.0
    llm_cost_usd: float = 0.0
    embedding_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    margin_usd: float = 0.0
    margin_pct: float | None = None
    by_agent: list[AdminCostByAgentRow] = Field(default_factory=list)


class AdminCostingPeriod(BaseModel):
    """A snapshot of platform-wide costing for one calendar window."""

    label: str
    period_start: datetime
    period_end: datetime
    revenue_usd: float = 0.0
    llm_cost_usd: float = 0.0
    embedding_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    gross_margin_usd: float = 0.0
    gross_margin_pct: float | None = None


class AdminPlatformCosting(BaseModel):
    period_label: str = "MTD vs prior month"
    current: AdminCostingPeriod
    prior: AdminCostingPeriod
    by_model: list[AdminCostByModelRow] = Field(default_factory=list)
    unknown_models: list[str] = Field(default_factory=list)
    embedding_model: str
    embedding_model_priced: bool = True
    cached_at: datetime
    cache_ttl_seconds: int


class AdminUserCostingRow(BaseModel):
    user_id: UUID
    email: str
    plan_slug: str | None = None
    plan_name: str | None = None
    revenue_usd: float = 0.0
    llm_cost_usd: float = 0.0
    embedding_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    margin_usd: float = 0.0
    margin_pct: float | None = None


class AdminCostingLeaderboard(BaseModel):
    metric: str  # "worst_margin" | "top_spend"
    items: list[AdminUserCostingRow] = Field(default_factory=list)
    cached_at: datetime
    cache_ttl_seconds: int


# ---------- Phase 4: Operations ----------------------------------------------------

# Worker heartbeat used both inside the Overview payload and by the System health page.
# We don't have a dedicated heartbeats table; these values are proxies derived from the
# tables the workers write to (indexing_jobs, usage_period_snapshots).


class AdminWorkerStatus(BaseModel):
    indexing_last_success_at: datetime | None = None
    indexing_last_attempt_at: datetime | None = None
    indexing_queue_depth: int = 0
    maintenance_last_computed_at: datetime | None = None


# ----- Stripe + indexing rows are referenced by the Overview, so define them first.


class AdminStripeEventRow(BaseModel):
    id: UUID
    stripe_event_id: str
    event_type: str
    processed_at: datetime


class AdminIndexingJobRow(BaseModel):
    id: UUID
    user_id: UUID
    user_email: str
    agent_id: UUID
    agent_name: str
    knowledge_source_id: UUID
    knowledge_source_title: str
    status: str
    attempt: int
    triggered_by: str
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    created_at: datetime


# ----- Overview ------------------------------------------------------------------


class AdminOverviewKpis(BaseModel):
    total_users: int = 0
    signups_today: int = 0
    signups_last_7d: int = 0
    active_subscriptions: int = 0
    mrr_usd: float = 0.0
    conversations_today: int = 0
    conversations_mtd: int = 0
    indexing_queue_depth: int = 0
    indexing_failed_24h: int = 0


class AdminOverview(BaseModel):
    kpis: AdminOverviewKpis
    recent_signups: list[AdminUserListItem] = Field(default_factory=list)
    recent_conversations: list[AdminConversationListItem] = Field(default_factory=list)
    recent_stripe_events: list[AdminStripeEventRow] = Field(default_factory=list)
    recent_indexing_failures: list[AdminIndexingJobRow] = Field(default_factory=list)
    workers: AdminWorkerStatus
    # Tiny costing summary copied from the Phase 3 overview so the home page doesn't
    # need a second round-trip. View-costing link goes to /admin/costing for the rest.
    llm_cost_mtd_usd: float = 0.0
    embedding_cost_mtd_usd: float = 0.0
    revenue_mtd_usd: float = 0.0
    gross_margin_mtd_usd: float = 0.0
    gross_margin_mtd_pct: float | None = None
    pricing_unknown_models: list[str] = Field(default_factory=list)


# ----- Agents --------------------------------------------------------------------


class AdminKnowledgeSourceRow(BaseModel):
    id: UUID
    user_id: UUID
    user_email: str
    agent_id: UUID
    agent_name: str
    type: str
    title: str
    status: str
    source_url: str | None = None
    last_indexed_at: datetime | None = None
    error_message: str | None = None
    chunks_count: int = 0
    chunks_total_tokens: int = 0
    created_at: datetime


class AdminAgentActionRow(BaseModel):
    id: UUID
    action_key: str
    enabled: bool
    config: dict[str, Any] = Field(default_factory=dict)
    safety_policy: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class AdminAgentListItem(BaseModel):
    id: UUID
    user_id: UUID
    user_email: str
    name: str
    slug: str
    status: str
    model: str
    conversations_total: int = 0
    conversations_mtd: int = 0
    knowledge_sources_count: int = 0
    actions_enabled_count: int = 0
    created_at: datetime
    archived_at: datetime | None = None


class AdminAgentListResponse(BaseModel):
    items: list[AdminAgentListItem]
    total: int
    page: int
    page_size: int


class AdminAgentDetail(AdminAgentListItem):
    system_prompt: str = ""
    behavior_settings: dict[str, Any] = Field(default_factory=dict)
    public_key: str
    knowledge_sources: list[AdminKnowledgeSourceRow] = Field(default_factory=list)
    actions: list[AdminAgentActionRow] = Field(default_factory=list)
    recent_conversations: list[AdminConversationListItem] = Field(default_factory=list)


# ----- Tickets -------------------------------------------------------------------


class AdminTicketListItem(BaseModel):
    id: UUID
    user_id: UUID
    user_email: str
    agent_id: UUID
    agent_name: str
    conversation_id: UUID
    status: str
    priority: str
    subject: str | None = None
    customer_email: str | None = None
    external_provider: str | None = None
    external_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminTicketListResponse(BaseModel):
    items: list[AdminTicketListItem]
    total: int
    page: int
    page_size: int


class AdminTicketDetail(AdminTicketListItem):
    metadata: dict[str, Any] = Field(default_factory=dict)
    conversation: AdminConversationListItem


# ----- Knowledge sources + indexing jobs -----------------------------------------


class AdminKnowledgeSourceListResponse(BaseModel):
    items: list[AdminKnowledgeSourceRow]
    total: int
    page: int
    page_size: int


class AdminKnowledgeChunkPreview(BaseModel):
    id: UUID
    chunk_index: int
    token_count: int
    content_preview: str
    created_at: datetime


class AdminKnowledgeSourceDetail(AdminKnowledgeSourceRow):
    storage_bucket: str | None = None
    storage_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    recent_jobs: list[AdminIndexingJobRow] = Field(default_factory=list)
    sample_chunks: list[AdminKnowledgeChunkPreview] = Field(default_factory=list)


class AdminIndexingJobListResponse(BaseModel):
    items: list[AdminIndexingJobRow]
    total: int
    page: int
    page_size: int


# ----- Billing -------------------------------------------------------------------


class AdminSubscriptionRow(BaseModel):
    id: UUID
    user_id: UUID
    user_email: str
    plan_id: UUID
    plan_slug: str
    plan_name: str
    monthly_price_cents: int
    status: str
    provider: str
    provider_customer_id: str | None = None
    provider_subscription_id: str | None = None
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    created_at: datetime


class AdminSubscriptionListResponse(BaseModel):
    items: list[AdminSubscriptionRow]
    total: int
    page: int
    page_size: int


class AdminUsageSnapshotRow(BaseModel):
    id: UUID
    user_id: UUID
    user_email: str
    period_start: date
    period_end: date
    included_conversations: int
    conversations_used: int
    overage_conversations: int
    estimated_overage_cents: int
    projected_conversations: int
    throttle_tier: str
    included_premium_turns: int = 0
    premium_turns_used: int = 0
    last_computed_at: datetime | None = None


class AdminUsageSnapshotListResponse(BaseModel):
    items: list[AdminUsageSnapshotRow]
    total: int
    page: int
    page_size: int


class AdminStripeEventListResponse(BaseModel):
    items: list[AdminStripeEventRow]
    total: int
    page: int
    page_size: int


# ----- Plans ---------------------------------------------------------------------


class AdminPlanRow(BaseModel):
    id: UUID
    slug: str
    name: str
    monthly_price_cents: int
    included_conversations: int
    overage_conversation_cents: int
    max_agents: int
    features: dict[str, Any] = Field(default_factory=dict)
    throttle_policy: dict[str, Any] = Field(default_factory=dict)
    is_active: bool
    public_on_pricing_page: bool = True
    sort_order: int = 0
    subscriptions_count: int = 0
    created_at: datetime


class AdminPlanListResponse(BaseModel):
    items: list[AdminPlanRow]


# ----- System --------------------------------------------------------------------


class AdminPricingStatus(BaseModel):
    llm_input_models: list[str] = Field(default_factory=list)
    llm_output_models: list[str] = Field(default_factory=list)
    embedding_models: list[str] = Field(default_factory=list)
    embedding_active_model: str
    embedding_active_model_priced: bool = True
    unknown_models_in_messages: list[str] = Field(default_factory=list)


class AdminSystemHealth(BaseModel):
    app_name: str
    app_version: str
    app_env: str
    database_ready: bool
    database_latency_ms: int
    workers: AdminWorkerStatus
    pricing_configured: AdminPricingStatus
