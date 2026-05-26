from typing import Any, Literal, cast
from urllib.parse import quote, urlparse
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.domains.plans.plan_limits import message_feedback_enabled_for_plan_slug
from app.domains.plans.subscription_queries import fetch_active_plan_slug
from app.domains.public_widget.schemas import PublicWidgetAgentContext, PublicWidgetConfigResponse

WidgetPosition = Literal["bottom_right", "bottom_left"]

FREE_PLAN_SLUG = "free"


def attachments_ui_enabled_for_plan_slug(plan_slug: str | None) -> bool:
    """Visitor file uploads in the widget — disabled until upload pipeline ships (marketing: Coming soon)."""
    _ = plan_slug
    return False


def hide_powered_by_chatrely_for_plan_slug(plan_slug: str | None) -> bool:
    """White-label: no attribution footer in the embed widget (Pro and legacy Scale)."""
    s = (plan_slug or "").strip().lower()
    return s in ("pro", "scale")


async def resolve_agent_for_widget_key(db: AsyncSession, public_key: str) -> PublicWidgetAgentContext:
    key = (public_key or "").strip()
    if not key:
        raise AppError(code="widget.missing_key", message="Missing agent key", status_code=401)

    row = (
        await db.execute(
            text(
                """
                select id, user_id, name, status, behavior_settings
                from public.agents
                where public_key = :pk
                limit 1
                """
            ),
            {"pk": key},
        )
    ).mappings().first()
    if row is None:
        raise AppError(code="widget.invalid_key", message="Unknown or invalid agent key", status_code=401)

    status = str(row["status"] or "")
    if status != "active":
        raise AppError(
            code="widget.agent_inactive",
            message="This agent is not accepting public chats",
            status_code=403,
        )

    behavior = row["behavior_settings"] if isinstance(row["behavior_settings"], dict) else {}
    return PublicWidgetAgentContext(
        agent_id=UUID(str(row["id"])),
        user_id=UUID(str(row["user_id"])),
        name=str(row["name"] or "Assistant"),
        behavior_settings=behavior,
    )


def build_public_widget_config(ctx: PublicWidgetAgentContext) -> PublicWidgetConfigResponse:
    b: dict[str, Any] = ctx.behavior_settings
    raw_pos = str(b.get("widget_position") or "bottom_right").lower().replace("-", "_")
    position: WidgetPosition = cast(WidgetPosition, raw_pos if raw_pos in ("bottom_right", "bottom_left") else "bottom_right")
    brand = b.get("brand_color")
    brand_color = str(brand).strip() if isinstance(brand, str) and brand.strip() else None
    raw_greeting = b.get("greeting_message")
    greeting_message = (
        str(raw_greeting).strip() if isinstance(raw_greeting, str) and raw_greeting.strip() else None
    )
    return PublicWidgetConfigResponse(
        agent_id=ctx.agent_id,
        name=ctx.name,
        brand_color=brand_color,
        widget_position=position,
        greeting_message=greeting_message,
    )


def _favicon_from_site_url(site_url: str) -> str | None:
    try:
        host = urlparse(site_url.strip()).hostname
        if not host:
            return None
        return f"https://www.google.com/s2/favicons?domain={quote(host, safe='')}&sz=128"
    except Exception:
        return None


async def build_public_widget_config_response(
    db: AsyncSession, ctx: PublicWidgetAgentContext
) -> PublicWidgetConfigResponse:
    """Public widget GET /config — includes escalation + optional favicon logo."""
    base = build_public_widget_config(ctx)

    human_row = (
        await db.execute(
            text(
                """
                select enabled
                from public.agent_actions
                where agent_id = cast(:aid as uuid)
                  and action_key = 'human.escalate'
                limit 1
                """
            ),
            {"aid": str(ctx.agent_id)},
        )
    ).mappings().first()
    human_ok = bool(human_row and human_row.get("enabled"))

    url_row = (
        await db.execute(
            text(
                """
                select source_url
                from public.knowledge_sources
                where agent_id = cast(:aid as uuid)
                  and type = 'website'
                  and coalesce(source_url, '') <> ''
                order by created_at asc
                limit 1
                """
            ),
            {"aid": str(ctx.agent_id)},
        )
    ).mappings().first()
    raw_url = str(url_row["source_url"]).strip() if url_row and url_row.get("source_url") else ""
    avatar_url = _favicon_from_site_url(raw_url) if raw_url else None

    plan_slug = await fetch_active_plan_slug(db, ctx.user_id)
    attachments_ui = attachments_ui_enabled_for_plan_slug(plan_slug)
    hide_powered = hide_powered_by_chatrely_for_plan_slug(plan_slug)
    message_feedback = message_feedback_enabled_for_plan_slug(plan_slug)

    return base.model_copy(
        update={
            "human_escalation_available": human_ok,
            "avatar_url": avatar_url,
            "attachments_ui_enabled": attachments_ui,
            "hide_powered_by_chatrely": hide_powered,
            "message_feedback_enabled": message_feedback,
        }
    )
