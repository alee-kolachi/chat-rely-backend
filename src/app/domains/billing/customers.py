"""Create and persist Stripe customers (workspace = user_id)."""

from __future__ import annotations

from uuid import UUID

import stripe
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.settings import get_settings
from app.domains.billing.stripe_client import configure_stripe


async def fetch_auth_user_email(db: AsyncSession, user_id: UUID) -> str | None:
    res = await db.execute(
        text("select email::text as email from auth.users where id = cast(:uid as uuid) limit 1"),
        {"uid": str(user_id)},
    )
    row = res.mappings().first()
    if not row or not row.get("email"):
        return None
    e = str(row["email"]).strip()
    return e or None


async def _latest_subscription_customer(db: AsyncSession, user_id: UUID) -> str | None:
    res = await db.execute(
        text(
            """
            select provider_customer_id
            from public.subscriptions
            where user_id = cast(:uid as uuid)
            order by created_at desc
            limit 1
            """
        ),
        {"uid": str(user_id)},
    )
    row = res.mappings().first()
    if not row:
        return None
    cid = row.get("provider_customer_id")
    return str(cid).strip() if cid else None


async def _update_subscription_customer(db: AsyncSession, user_id: UUID, customer_id: str) -> None:
    await db.execute(
        text(
            """
            update public.subscriptions s
            set provider_customer_id = :cid, updated_at = now()
            from (
              select id from public.subscriptions
              where user_id = cast(:uid as uuid)
              order by created_at desc
              limit 1
            ) pick
            where s.id = pick.id
              and (s.provider_customer_id is null or s.provider_customer_id = '')
            """
        ),
        {"uid": str(user_id), "cid": customer_id},
    )


async def ensure_stripe_customer_for_user(
    db: AsyncSession,
    *,
    user_id: UUID,
    shop_domain: str | None = None,
    email: str | None = None,
) -> str | None:
    """
    Ensure a Stripe Customer exists and provider_customer_id is set on the latest subscription row.
    Returns customer id or None if Stripe is not configured.
    """
    settings = get_settings()
    if not (settings.stripe_secret_key or "").strip():
        return None

    existing = await _latest_subscription_customer(db, user_id)
    if existing:
        return existing

    resolved_email = email
    if not resolved_email:
        resolved_email = await fetch_auth_user_email(db, user_id)

    configure_stripe()
    meta: dict[str, str] = {"supabase_user_id": str(user_id)}
    if shop_domain:
        meta["shop_domain"] = shop_domain[:200]

    kwargs: dict[str, object] = {"metadata": meta}
    if resolved_email and "@" in resolved_email:
        kwargs["email"] = resolved_email[:256]

    try:
        customer = stripe.Customer.create(**kwargs)
    except stripe.StripeError as exc:
        raise AppError(
            code="stripe.customer_failed",
            message="Could not create Stripe customer",
            status_code=502,
            details={"stripe": str(exc)[:300]},
        ) from exc

    cid = str(customer.id)
    await _update_subscription_customer(db, user_id, cid)
    return cid
