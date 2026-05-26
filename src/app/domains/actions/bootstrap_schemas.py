from pydantic import BaseModel, ConfigDict

from app.domains.actions.schemas import ActionCatalogResponse
from app.domains.integrations.shopify.schemas import ShopifyConnectionStatus


class AgentWebsitePreview(BaseModel):
    """First website source used for Playground favicon (matches dashboard prioritization)."""

    model_config = ConfigDict(extra="forbid")

    source_url: str
    website_mode: str | None = None
    title: str | None = None


class AgentIntegrationsBootstrapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog: ActionCatalogResponse
    shopify: ShopifyConnectionStatus
    website_preview: AgentWebsitePreview | None = None
