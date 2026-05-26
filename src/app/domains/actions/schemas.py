from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ActionCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    action_key: str
    label: str
    description: str
    status: Literal["live", "coming_soon", "blocked_by_plan"]
    required_scopes: list[str]
    """Granted OAuth scopes from the connected store (subset relevant to Shopify)."""
    connection_scopes: list[str] = Field(default_factory=list)
    scopes_satisfied: bool
    enabled: bool
    config: dict[str, Any] = Field(default_factory=dict)
    safety_policy: dict[str, Any] = Field(default_factory=dict)


class ActionCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[ActionCatalogEntry]
    max_enabled_shopify_actions: int = 0
    enabled_shopify_actions: int = 0


class AgentActionPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    config: dict[str, Any] | None = None
    safety_policy: dict[str, Any] | None = None
