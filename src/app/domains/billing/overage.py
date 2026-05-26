"""Conversation overage invoicing (Stripe). Snapshots currently keep ``estimated_overage_cents`` at 0, so this path is inactive until metered billing is enabled."""

from __future__ import annotations

import json
from datetime import date
from uuid import UUID

import stripe
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.settings import get_settings
from app.domains.billing.stripe_client import configure_stripe

log = structlog.get_logger(__name__)

META_LAST_OVERAGE_KEY = "last_overage_billed_period_end"


async def charge_conversation_overage_for_user_period(
    db: AsyncSession,
    *,
    user_id: UUID,
    period_end: date,
) -> str | None:
    """
    If usage snapshot shows estimated_overage_cents > 0 for the user's subscription period ending
    on period_end, create a Stripe invoice item + invoice and return invoice id.
    Idempotent via subscriptions.metadata last_overage_billed_period_end.
    """
    settings = get_settings()
    if not (settings.stripe_secret_key or "").strip():
        raise AppError(code="stripe.not_configured", message="Stripe not configured", status_code=503)

    row = (
        await db.execute(
            text(
                """
                select
                  s.id as subscription_id,
                  s.provider_customer_id,
                  s.provider_subscription_id,
                  s.metadata,
                  u.estimated_overage_cents,
                  u.period_start,
                  u.period_end
                from public.subscriptions s
                join public.usage_period_snapshots u
                  on u.user_id = s.user_id
                 and u.period_end = cast(:pe as date)
                where s.user_id = cast(:uid as uuid)
                  and s.provider_customer_id is not null
                  and trim(s.provider_customer_id) <> ''
                  and s.status in ('active', 'trialing', 'past_due')
                order by s.created_at desc
                limit 1
                """
            ),
            {"uid": str(user_id), "pe": period_end},
        )
    ).mappings().first()

    if not row:
        log.info("overage.skip_no_row", user_id=str(user_id), period_end=str(period_end))
        return None

    customer_id = str(row["provider_customer_id"] or "").strip()
    if not customer_id:
        return None

    cents = int(row["estimated_overage_cents"] or 0)
    if cents <= 0:
        log.info("overage.skip_zero", user_id=str(user_id))
        return None

    meta_raw = row.get("metadata") or {}
    if isinstance(meta_raw, str):
        meta = json.loads(meta_raw) if meta_raw else {}
    else:
        meta = dict(meta_raw) if meta_raw else {}

    if str(meta.get(META_LAST_OVERAGE_KEY) or "") == str(period_end):
        log.info("overage.skip_already_billed", user_id=str(user_id), period_end=str(period_end))
        return None

    configure_stripe()
    description = f"Conversation overage (above included allowance) for cycle ending {period_end.isoformat()}"

    try:
        inv = stripe.Invoice.create(
            customer=customer_id,
            collection_method="charge_automatically",
            auto_advance=False,
            description=description[:500],
        )
        stripe.InvoiceItem.create(
            customer=customer_id,
            invoice=inv.id,
            amount=cents,
            currency="usd",
            description=description[:500],
        )
        final_inv = stripe.Invoice.finalize_invoice(inv.id)
        st = getattr(final_inv, "status", None) if not isinstance(final_inv, dict) else final_inv.get("status")
        inv_id = getattr(final_inv, "id", None) if not isinstance(final_inv, dict) else final_inv.get("id")
        if st == "open" and inv_id:
            stripe.Invoice.pay(str(inv_id))
    except stripe.StripeError as exc:
        raise AppError(
            code="stripe.overage_invoice_failed",
            message="Could not charge overage invoice",
            status_code=502,
            details={"stripe": str(exc)[:400]},
        ) from exc

    meta[META_LAST_OVERAGE_KEY] = str(period_end)
    await db.execute(
        text(
            """
            update public.subscriptions
            set metadata = cast(:meta as jsonb), updated_at = now()
            where id = cast(:sid as uuid)
            """
        ),
        {"meta": json.dumps(meta), "sid": str(row["subscription_id"])},
    )

    out_id = getattr(final_inv, "id", None) if not isinstance(final_inv, dict) else final_inv.get("id")
    return str(out_id) if out_id else None
