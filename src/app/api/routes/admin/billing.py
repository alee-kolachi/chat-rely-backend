from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_db, require_admin
from app.domains.admin.billing_service import (
    list_admin_stripe_events,
    list_admin_subscriptions,
    list_admin_usage_snapshots,
)
from app.domains.admin.schemas import (
    AdminStripeEventListResponse,
    AdminSubscriptionListResponse,
    AdminUsageSnapshotListResponse,
)


router = APIRouter()


@router.get("/billing/subscriptions", response_model=AdminSubscriptionListResponse)
async def list_admin_subscriptions_route(
    user_email: str | None = Query(default=None),
    plan_slug: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminSubscriptionListResponse:
    return await list_admin_subscriptions(
        db,
        user_email=user_email,
        plan_slug=plan_slug,
        status=status,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/billing/usage-snapshots", response_model=AdminUsageSnapshotListResponse
)
async def list_admin_usage_snapshots_route(
    user_email: str | None = Query(default=None),
    throttle_tier: str | None = Query(default=None, description="normal|soft|strong"),
    period_start_after: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUsageSnapshotListResponse:
    return await list_admin_usage_snapshots(
        db,
        user_email=user_email,
        throttle_tier=throttle_tier,
        period_start_after=period_start_after,
        page=page,
        page_size=page_size,
    )


@router.get("/billing/stripe-events", response_model=AdminStripeEventListResponse)
async def list_admin_stripe_events_route(
    event_type: str | None = Query(default=None),
    processed_after: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminStripeEventListResponse:
    return await list_admin_stripe_events(
        db,
        event_type=event_type,
        processed_after=processed_after,
        page=page,
        page_size=page_size,
    )
