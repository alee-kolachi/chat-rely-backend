"""Mailjet Send API v3.1 (transactional)."""

from __future__ import annotations

import base64
from typing import Any

import httpx
import structlog

from app.core.settings import get_settings

log = structlog.get_logger("mailjet.client")


async def send_email(
    *,
    to_email: str,
    subject: str,
    text_part: str,
    html_part: str | None = None,
    reply_to_email: str | None = None,
    custom_id: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    key = (settings.mailjet_api_key or "").strip()
    secret = (settings.mailjet_api_secret or "").strip()
    if not key or not secret:
        raise RuntimeError("Mailjet API credentials not configured")

    from_email = (settings.mailjet_sender_email or "").strip()
    from_name = (settings.mailjet_sender_name or "Support").strip()
    if not from_email:
        raise RuntimeError("MAILJET_SENDER_EMAIL not configured")

    auth = base64.b64encode(f"{key}:{secret}".encode()).decode("ascii")
    msg: dict[str, Any] = {
        "From": {"Email": from_email, "Name": from_name},
        "To": [{"Email": to_email}],
        "Subject": subject,
        "TextPart": text_part,
    }
    if html_part:
        msg["HTMLPart"] = html_part
    if reply_to_email:
        msg["ReplyTo"] = {"Email": reply_to_email}
    if custom_id:
        msg["CustomID"] = custom_id

    body = {"Messages": [msg]}
    url = "https://api.mailjet.com/v3.1/send"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, json=body, headers={"Authorization": f"Basic {auth}"})
    if r.status_code >= 400:
        log.warning("mailjet.send_failed", status=r.status_code, body=r.text[:500])
        r.raise_for_status()
    data = r.json()
    log.info("mailjet.sent", to=to_email, subject=subject[:80])
    return data if isinstance(data, dict) else {}
