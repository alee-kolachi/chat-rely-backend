"""Mailjet Parse API → append inbound email as a user message on a conversation."""

from __future__ import annotations

import re
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.settings import get_settings
from app.domains.conversations.schemas import ConversationMessageCreateRequest
from app.domains.conversations.service import append_message
from app.domains.integrations.mailjet.reply_token import decode_reply_token

log = structlog.get_logger("mailjet.inbound")

router = APIRouter(tags=["mailjet"])


def _form_str(form: Any, *keys: str) -> str:
    for k in keys:
        v = form.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


@router.post("/integrations/mailjet/inbound")
async def mailjet_parse_inbound(
    request: Request,
    verify: str | None = Query(None, description="Shared secret from MAILJET_INBOUND_WEBHOOK_SECRET"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    settings = get_settings()
    expected = (settings.mailjet_inbound_webhook_secret or "").strip()
    if expected and verify != expected:
        raise HTTPException(status_code=401, detail="Invalid verify")

    form = await request.form()
    recipient = _form_str(form, "Recipient", "recipient", "To")
    text = _form_str(form, "Text-part", "text-part", "Text", "text", "stripped-text")

    m = re.search(r"\+([^+@]+)@", recipient)
    if not m:
        log.warning("mailjet.inbound.no_token", recipient=recipient[:120])
        raise HTTPException(status_code=422, detail="Could not parse reply recipient")

    secret = (settings.mailjet_reply_hmac_secret or settings.integration_oauth_state_secret or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Reply token secret not configured")

    parsed = decode_reply_token(m.group(1), secret)
    if parsed is None:
        raise HTTPException(status_code=422, detail="Invalid reply token")

    conversation_id, user_id = parsed
    body = text or "(empty message)"
    meta = {"source": "email_inbound", "subject": _form_str(form, "Subject", "subject")[:500]}

    await append_message(
        db,
        user_id=user_id,
        conversation_id=conversation_id,
        payload=ConversationMessageCreateRequest(
            role="user",
            content=body[:120000],
            metadata=meta,
        ),
    )

    log.info("mailjet.inbound.appended", conversation_id=str(conversation_id))
    return {"status": "ok"}
