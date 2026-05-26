from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, computed_field

from app.domains.plans.plan_limits import AdvancedResolutionBand, advanced_resolution_band
from app.domains.plans.schemas import PlanLimitsDTO


class ProfileDTO(BaseModel):
    id: UUID
    full_name: str | None
    avatar_url: str | None
    timezone: str
    email_notifications_enabled: bool
    notification_preferences: dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime


class PlanDTO(BaseModel):
    id: UUID
    slug: str
    name: str
    monthly_price_cents: int = 0
    included_conversations: int
    max_agents: int
    overage_conversation_cents: int
    features: dict[str, Any]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def limits(self) -> PlanLimitsDTO:
        from app.domains.plans.plan_limits import plan_limits_dto_from_row

        return plan_limits_dto_from_row(
            included_conversations=self.included_conversations,
            max_agents=self.max_agents,
            features=self.features,
        )


class SubscriptionDTO(BaseModel):
    id: UUID
    user_id: UUID
    plan_id: UUID
    status: str
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    provider_customer_id: str | None = None
    provider_subscription_id: str | None = None


class UsageSnapshotDTO(BaseModel):
    period_start: date
    period_end: date
    included_conversations: int
    conversations_used: int
    overage_conversations: int
    estimated_overage_cents: int
    throttle_tier: str
    included_premium_turns: int = 0
    premium_turns_used: int = 0


class BootstrapResponse(BaseModel):
    profile: ProfileDTO
    subscription: SubscriptionDTO
    plan: PlanDTO
    onboarding_completed: bool


class MeContextResponse(BaseModel):
    profile: ProfileDTO
    subscription: SubscriptionDTO
    plan: PlanDTO
    usage_snapshot: UsageSnapshotDTO | None
    onboarding_completed: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def advanced_resolution_band(self) -> AdvancedResolutionBand:
        snap = self.usage_snapshot
        if snap is None:
            return "standard_only"
        return advanced_resolution_band(
            plan_slug=self.plan.slug,
            included_premium_turns=snap.included_premium_turns,
            premium_turns_used=snap.premium_turns_used,
        )


class OnboardingGateResponse(BaseModel):
    onboarding_completed: bool

