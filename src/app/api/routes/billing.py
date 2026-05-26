"""Authenticated billing: Checkout and subscription changes."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user, get_db
from app.core.settings import get_settings
from app.domains.billing.checkout_service import (
    change_subscription_plan,
    create_billing_portal_session,
    create_subscription_checkout_session,
    finalize_subscription_checkout_session,
)
from app.domains.billing.subscription_sync import sync_user_subscription_from_stripe
from app.domains.billing.overage import charge_conversation_overage_for_user_period

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutRequest(BaseModel):
    plan_slug: str = Field(min_length=1, max_length=64)
    interval: str = Field(default="month", pattern="^month$")
    return_context: str = Field(default="account", pattern="^(account|onboarding|marketing)$")
    agent_id: UUID | None = None
    return_origin: str | None = Field(default=None, max_length=256)


@router.post("/checkout")
async def billing_checkout(
    body: CheckoutRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    url = await create_subscription_checkout_session(
        db,
        user_id=user.user_id,
        plan_slug=body.plan_slug,
        interval=body.interval,
        return_context=body.return_context,
        agent_id=body.agent_id,
        return_origin=body.return_origin,
    )
    await db.commit()
    return {"url": url}


class CheckoutCompleteRequest(BaseModel):
    checkout_session_id: str = Field(min_length=8, max_length=128)


@router.post("/checkout/complete")
async def billing_checkout_complete(
    body: CheckoutCompleteRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Apply a completed Stripe Checkout session to the workspace (webhook fallback for localhost)."""
    await finalize_subscription_checkout_session(
        db,
        user_id=user.user_id,
        checkout_session_id=body.checkout_session_id,
    )
    await db.commit()
    return {"status": "ok"}


class SubscriptionChangeRequest(BaseModel):
    plan_slug: str = Field(min_length=1, max_length=64)
    proration_behavior: str = Field(default="create_prorations")


@router.post("/subscription/change")
async def billing_subscription_change(
    body: SubscriptionChangeRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    await change_subscription_plan(
        db,
        user_id=user.user_id,
        new_plan_slug=body.plan_slug,
        proration_behavior=body.proration_behavior,
    )
    await db.commit()
    return {"status": "ok"}


class PortalRequest(BaseModel):
    return_context: str = Field(default="plan", pattern="^(plan|billing)$")
    return_origin: str | None = Field(default=None, max_length=256)


@router.post("/portal")
async def billing_customer_portal(
    request: Request,
    body: PortalRequest = PortalRequest(),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Redirect URL for Stripe Customer Portal (invoices, payment methods, cancel subscription)."""
    return_context = body.return_context
    url = await create_billing_portal_session(
        db,
        user_id=user.user_id,
        return_context=return_context,
        return_origin=body.return_origin or request.headers.get("origin"),
    )
    await db.commit()
    return {"url": url}


@router.post("/sync")
async def billing_sync_subscription(
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Apply the workspace's Stripe subscription state to the database (webhook fallback)."""
    result = await sync_user_subscription_from_stripe(db, user_id=user.user_id)
    await db.commit()
    return result


class InternalOverageRequest(BaseModel):
    user_id: UUID
    period_end: date


@router.post("/internal/charge-overage")
async def billing_internal_charge_overage(
    body: InternalOverageRequest,
    db: AsyncSession = Depends(get_db),
    x_billing_secret: str | None = Header(default=None, alias="X-Billing-Secret"),
) -> dict[str, str | None]:
    settings = get_settings()
    expected = (settings.billing_internal_secret or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="BILLING_INTERNAL_SECRET is not configured")
    if (x_billing_secret or "").strip() != expected:
        raise HTTPException(status_code=401, detail="Invalid billing secret")

    sub = (
        await db.execute(
            text(
                """
                select current_period_start, current_period_end
                from public.subscriptions
                where user_id = cast(:uid as uuid)
                order by created_at desc
                limit 1
                """
            ),
            {"uid": str(body.user_id)},
        )
    ).mappings().first()
    if sub:
        ps = sub["current_period_start"].date()
        pe = sub["current_period_end"].date()
        await db.execute(
            text(
                "select public.refresh_usage_period_snapshot(cast(:uid as uuid), cast(:ps as date), cast(:pe as date))"
            ),
            {"uid": str(body.user_id), "ps": ps, "pe": pe},
        )

    inv_id = await charge_conversation_overage_for_user_period(
        db, user_id=body.user_id, period_end=body.period_end
    )
    await db.commit()
    return {"invoice_id": inv_id}
