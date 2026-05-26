"""Email customer when agent replies from dashboard (offline / email path)."""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.actions.human_availability import seller_is_available_for_live_chat
from app.domains.actions.service import get_human_escalation_for_runtime
from app.domains.conversations.service import get_conversation
from app.domains.integrations.mailjet.client import send_email
from app.domains.integrations.mailjet.reply_token import encode_reply_token
from app.domains.tickets.service import get_ticket_by_conversation
from app.core.settings import get_settings

log = structlog.get_logger("mailjet.notify")


async def maybe_send_ticket_email_reply(
    db: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    assistant_content: str,
) -> None:
    """If conversation is escalated, seller is not \"live\", and visitor email exists — send via Mailjet."""
    settings = get_settings()
    if not (settings.mailjet_api_key and settings.mailjet_api_secret and settings.mailjet_sender_email):
        return

    conv = await get_conversation(db, user_id, conversation_id)
    if conv.status != "escalated":
        return

    meta = dict(conv.metadata or {})
    visitor_email = (meta.get("visitor_email") or "").strip()
    if not visitor_email:
        return

    ticket = await get_ticket_by_conversation(db, user_id, conversation_id)
    if ticket is None:
        return

    enabled, cfg = await get_human_escalation_for_runtime(db, user_id=user_id, agent_id=conv.agent_id)
    if not enabled:
        return
    if seller_is_available_for_live_chat(cfg):
        return

    secret = (settings.mailjet_reply_hmac_secret or settings.integration_oauth_state_secret or "").strip()
    if not secret:
        log.warning("mailjet.reply_hmac_missing")
        return

    domain = (settings.mailjet_inbound_domain or "").strip()
    if not domain:
        log.warning("mailjet.inbound_domain_missing_skip_email")
        return

    token = encode_reply_token(conversation_id, user_id, secret)
    reply_addr = f"reply+{token}@{domain}"

    subject = ticket.subject or "Re: your conversation"
    try:
        await send_email(
            to_email=visitor_email,
            subject=subject,
            text_part=assistant_content,
            html_part=f"<p>{assistant_content.replace(chr(10), '<br/>')}</p>",
            reply_to_email=reply_addr,
            custom_id=str(conversation_id),
        )
    except Exception as exc:
        log.warning("mailjet.notify_failed", error=str(exc)[:200])
