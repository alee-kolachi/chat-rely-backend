from typing import Any

from pydantic import BaseModel, ConfigDict, computed_field


class PlanLimitsDTO(BaseModel):
    """Enforced limits for the current catalog (mirrors marketing matrix + ``public.plans``)."""

    model_config = ConfigDict(frozen=True)

    included_conversations: int
    max_agents: int
    max_enabled_actions_per_agent: int
    max_total_knowledge_bytes: int
    max_website_crawl_bytes: int


class PublicPlanDTO(BaseModel):
    slug: str
    name: str
    monthly_price_cents: int
    included_conversations: int
    overage_conversation_cents: int
    max_agents: int
    features: dict[str, Any]
    throttle_policy: dict[str, Any]
    sort_order: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def limits(self) -> PlanLimitsDTO:
        from app.domains.plans.plan_limits import plan_limits_dto_from_row

        return plan_limits_dto_from_row(
            included_conversations=self.included_conversations,
            max_agents=self.max_agents,
            features=self.features,
        )
