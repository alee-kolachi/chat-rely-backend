"""Human escalation helpers for the chat agent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

_HUMAN_INTENT = re.compile(
    r"(?i)\b("
    r"talk\s+to\s+((a|an)\s+)?human|"
    r"speak\s+(to|with)\s+((a|an)\s+)?(human|person|representative|agent|someone)|"
    r"human\s+(representative|agent|support)|"
    r"real\s+person|"
    r"live\s+(agent|person|representative|support)|"
    r"connect\s+me\s+with\s+((a|an)\s+)?(human|person|someone|support|agent)|"
    r"(need|want)\s+to\s+talk\s+to\s+((a|an)\s+)?(human|person|agent|someone)|"
    r"can\s+i\s+speak\s+to\s+((a|an)\s+)?(human|person|agent|someone)|"
    r"get\s+me\s+((a|an)\s+)?(human|person|agent)|"
    r"escalate\s+to\s+((a|an)\s+)?(human|agent|person)"
    r")\b"
)


def message_requests_human(text: str) -> bool:
    return bool(_HUMAN_INTENT.search((text or "").strip()))

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import text

from app.domains.actions.human_availability import seller_is_available_for_live_chat
from app.domains.conversations.service import OPERATOR_ENGAGED_META_KEY, get_conversation
from app.domains.runtime.schemas import RuntimeEscalationInfo
from app.domains.tickets.service import record_escalation, update_visitor_email_metadata


def normalize_conversation_status(status: Any) -> str:
    raw = str(status or "open").strip().lower()
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    return raw.strip("'\"")


async def conversation_is_awaiting_human_team(
    db: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
) -> bool:
    """True when the thread is escalated, has a ticket, or an operator took over."""
    result = await db.execute(
        text(
            """
            select c.status::text as status, c.metadata
            from public.conversations c
            where c.id = :conversation_id and c.user_id = :user_id
            """
        ),
        {"conversation_id": str(conversation_id), "user_id": str(user_id)},
    )
    row = result.mappings().first()
    if row is None:
        return False
    if normalize_conversation_status(row["status"]) == "escalated":
        return True
    meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
    if bool(meta.get(OPERATOR_ENGAGED_META_KEY)):
        return True
    ticket = await db.execute(
        text(
            """
            select 1
            from public.tickets t
            where t.conversation_id = :conversation_id
              and t.user_id = :user_id
            limit 1
            """
        ),
        {"conversation_id": str(conversation_id), "user_id": str(user_id)},
    )
    return ticket.first() is not None


def handoff_reply_for_status(
    *,
    conversation_status: str,
    esc_cfg: dict[str, Any],
) -> str:
    if normalize_conversation_status(conversation_status) == "escalated":
        return handoff_reply_awaiting_team()
    return handoff_reply_open(
        seller_live=seller_is_available_for_live_chat(esc_cfg),
        estimated_minutes=int(esc_cfg.get("estimated_response_minutes", 15)),
    )


def handoff_reply_open(*, seller_live: bool, estimated_minutes: int) -> str:
    if seller_live:
        n = max(1, int(estimated_minutes))
        return (
            f"I’ve connected you with our team. Someone should reply within about {n} minutes. "
            "If you think of anything else, you can add it here."
        )
    return (
        "I’ve passed this to our team. We’re not available for live chat at the moment, "
        "but you’ll get an email follow-up as soon as someone can help."
    )


def handoff_reply_already_escalated() -> str:
    return handoff_reply_awaiting_team()


def handoff_reply_awaiting_team() -> str:
    return (
        "Your message is with our team. "
        "They'll get back to you as soon as they can. You can add more here anytime."
    )


def visitor_empty_reply_fallback() -> str:
    return (
        "I'm not sure about that right now. "
        "Try asking in another way, or contact our support team if you need more help."
    )


@dataclass(frozen=True)
class EscalationTurnContext:
    user_id: UUID
    agent_id: UUID
    conversation_id: UUID
    user_message: str
    visitor_email: str | None
    esc_cfg: dict[str, Any]


async def persist_visitor_email(
    db: AsyncSession,
    *,
    ctx: EscalationTurnContext,
) -> None:
    email = (ctx.visitor_email or "").strip()
    if not email:
        return
    await update_visitor_email_metadata(
        db,
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        visitor_email=email,
    )


async def perform_escalation(
    db: AsyncSession,
    *,
    ctx: EscalationTurnContext,
) -> bool:
    """Record ticket + escalated status when the thread is still eligible."""
    conv = await get_conversation(db, ctx.user_id, ctx.conversation_id)
    if conv.status in ("escalated", "resolved", "idle_closed"):
        return False
    meta = dict(conv.metadata or {})
    visitor_email = (meta.get("visitor_email") or ctx.visitor_email or "").strip() or None
    await record_escalation(
        db,
        user_id=ctx.user_id,
        agent_id=ctx.agent_id,
        conversation_id=ctx.conversation_id,
        user_message=ctx.user_message,
        customer_email=visitor_email,
    )
    return True


def build_escalation_info(
    *,
    human_enabled: bool,
    esc_cfg: dict[str, Any],
    occurred: bool,
) -> RuntimeEscalationInfo:
    seller_live = seller_is_available_for_live_chat(esc_cfg) if human_enabled else False
    est_min = int(esc_cfg.get("estimated_response_minutes", 15)) if human_enabled else 15
    channel_hint: Literal["live", "email"] | None = (
        ("live" if seller_live else "email") if human_enabled else None
    )
    return RuntimeEscalationInfo(
        human_escalation_action_enabled=human_enabled,
        occurred=occurred,
        seller_live=seller_live if human_enabled else False,
        estimated_minutes=est_min if human_enabled and seller_live else None,
        channel_hint=channel_hint,
    )


def escalation_tool_system_appendix() -> str:
    return (
        "You have one action: `escalate_to_human`. Call it when the visitor asks for a person or "
        "human support, or when you cannot resolve their issue and they need your team. "
        "Do not tell them they are connected to a human unless you have called this tool."
    )
