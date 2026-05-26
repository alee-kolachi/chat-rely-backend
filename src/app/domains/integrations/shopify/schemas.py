from pydantic import BaseModel, ConfigDict, Field


class ShopifyOAuthStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization_url: str


class ShopifyConnectionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connected: bool
    shop_domain: str | None = None
    scopes: list[str] = Field(default_factory=list)
    status: str = "disconnected"
    last_synced_at: str | None = None
