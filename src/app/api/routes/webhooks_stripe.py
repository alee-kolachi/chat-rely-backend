"""Stripe webhooks (unsigned; verified via Stripe-Signature)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.domains.billing.webhooks import (
    claim_stripe_event,
    process_stripe_event,
    verify_stripe_payload,
)

log = structlog.get_logger(__name__)

router = APIRouter(tags=["webhooks-stripe"])


@router.post("/webhooks/stripe")
async def stripe_webhook_route(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        event = verify_stripe_payload(payload=payload, sig_header=sig)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not await claim_stripe_event(db, stripe_event_id=str(event.id), event_type=str(event.type)):
        await db.commit()
        return {"received": True, "duplicate": True}

    try:
        await process_stripe_event(db, event)
        await db.commit()
    except Exception:
        log.exception("stripe.webhook.process_failed", event_id=str(event.id), event_type=str(event.type))
        await db.rollback()
        raise HTTPException(status_code=500, detail="Webhook processing failed") from None

    return {"received": True, "duplicate": False}
