from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PublicWidgetAgentContext(BaseModel):
    """Resolved merchant scope for a browser-embeddable widget (via agent public_key)."""

    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    user_id: UUID
    name: str
    behavior_settings: dict[str, Any]


class PublicWidgetConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    name: str
    brand_color: str | None = None
    widget_position: Literal["bottom_right", "bottom_left"] = "bottom_right"
    """Optional first assistant bubble shown when the chat opens."""
    greeting_message: str | None = None
    """True when ``human.escalate`` is enabled for this agent (widget may show Escalate button)."""
    human_escalation_available: bool = False
    """Optional logo URL for header (e.g. favicon from primary website knowledge source)."""
    avatar_url: str | None = None
    """When True, widget may show a visitor attachment affordance. Disabled until uploads ship (marketing: Coming soon)."""
    attachments_ui_enabled: bool = False
    hide_powered_by_chatrely: bool = Field(
        default=False,
        description=(
            "When True, omit “Powered by ChatRely” in the embed (Pro and legacy Scale). "
            "When False, the widget may show it only until the visitor sends their first message."
        ),
    )
    message_feedback_enabled: bool = Field(
        default=False,
        description="When True, embed may show thumbs up/down on assistant replies (Pro / Scale).",
    )


class PublicWidgetChatRequest(BaseModel):
    """Visitor chat — agent is implied by ``X-ChatRely-Agent-Key`` (no overrides)."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=8000)
    conversation_id: UUID | None = None
    visitor_id: str = Field(min_length=1, max_length=255)
    visitor_email: str | None = None
    request_human: bool = False
    locale: str | None = None
    country_code: str | None = None


class PublicWidgetMessageFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    visitor_id: str = Field(min_length=1, max_length=255)
    remove: bool = False
    value: Literal[-1, 1] | None = None

    @model_validator(mode="after")
    def _value_or_remove(self) -> Self:
        if self.remove:
            return self
        if self.value is None:
            raise ValueError("value is required when remove is false")
        return self
