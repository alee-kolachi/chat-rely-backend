"""Cross-tenant billing reads for the admin panel.

Three flat browsers: subscriptions (current state from Stripe sync), usage snapshots
(MTD billing book-keeping), and stripe webhook events (the audit trail of what we've
acked from Stripe). All three are read-only.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.admin.schemas import (
    AdminStripeEventListResponse,
    AdminStripeEventRow,
    AdminSubscriptionListResponse,
    AdminSubscriptionRow,
    AdminUsageSnapshotListResponse,
    AdminUsageSnapshotRow,
)


async def list_admin_subscriptions(
    db: AsyncSession,
    *,
    user_email: str | None = None,
    plan_slug: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> AdminSubscriptionListResponse:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    offset = (page - 1) * page_size

    email_needle = (user_email or "").strip() or None
    where_clauses: list[str] = []
    params: dict[str, object] = {"limit": page_size, "offset": offset}

    if email_needle:
        where_clauses.append("u.email ilike '%' || :user_email || '%'")
        params["user_email"] = email_needle
    if plan_slug:
        where_clauses.append("p.slug = :plan_slug")
        params["plan_slug"] = plan_slug
    if status:
        where_clauses.append("s.status = cast(:status as public.subscription_status)")
        params["status"] = status

    where_sql = ("where " + " and ".join(where_clauses)) if where_clauses else ""

    list_sql = f"""
        select
          s.id, s.user_id, coalesce(u.email, '') as user_email,
          p.id as plan_id, p.slug as plan_slug, p.name as plan_name,
          p.monthly_price_cents,
          s.status::text as status, s.provider,
          s.provider_customer_id, s.provider_subscription_id,
          s.current_period_start, s.current_period_end, s.cancel_at_period_end,
          s.created_at
        from public.subscriptions s
        join public.plans p on p.id = s.plan_id
        join auth.users u on u.id = s.user_id
        {where_sql}
        order by s.created_at desc, s.id asc
        limit :limit offset :offset
    """

    count_sql = f"""
        select count(*)::int as total
        from public.subscriptions s
        join public.plans p on p.id = s.plan_id
        join auth.users u on u.id = s.user_id
        {where_sql}
    """

    list_result = await db.execute(text(list_sql), params)
    items = [AdminSubscriptionRow.model_validate(r) for r in list_result.mappings().all()]

    count_params = {k: v for k, v in params.items() if k not in {"limit", "offset"}}
    count_result = await db.execute(text(count_sql), count_params)
    total = int(count_result.scalar_one())

    return AdminSubscriptionListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


async def list_admin_usage_snapshots(
    db: AsyncSession,
    *,
    user_email: str | None = None,
    throttle_tier: str | None = None,
    period_start_after: date | None = None,
    page: int = 1,
    page_size: int = 50,
) -> AdminUsageSnapshotListResponse:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    offset = (page - 1) * page_size

    email_needle = (user_email or "").strip() or None
    where_clauses: list[str] = []
    params: dict[str, object] = {"limit": page_size, "offset": offset}

    if email_needle:
        where_clauses.append("u.email ilike '%' || :user_email || '%'")
        params["user_email"] = email_needle
    if throttle_tier:
        where_clauses.append("ups.throttle_tier = cast(:throttle_tier as public.throttle_tier)")
        params["throttle_tier"] = throttle_tier
    if period_start_after is not None:
        where_clauses.append("ups.period_start >= :period_start_after")
        params["period_start_after"] = period_start_after

    where_sql = ("where " + " and ".join(where_clauses)) if where_clauses else ""

    list_sql = f"""
        select
          ups.id, ups.user_id, coalesce(u.email, '') as user_email,
          ups.period_start, ups.period_end,
          ups.included_conversations, ups.conversations_used, ups.overage_conversations,
          ups.estimated_overage_cents, ups.projected_conversations,
          ups.throttle_tier::text as throttle_tier,
          coalesce(ups.included_premium_turns, 0) as included_premium_turns,
          coalesce(ups.premium_turns_used, 0) as premium_turns_used,
          ups.last_computed_at
        from public.usage_period_snapshots ups
        join auth.users u on u.id = ups.user_id
        {where_sql}
        order by ups.period_start desc, ups.id asc
        limit :limit offset :offset
    """

    count_sql = f"""
        select count(*)::int as total
        from public.usage_period_snapshots ups
        join auth.users u on u.id = ups.user_id
        {where_sql}
    """

    list_result = await db.execute(text(list_sql), params)
    items = [AdminUsageSnapshotRow.model_validate(r) for r in list_result.mappings().all()]

    count_params = {k: v for k, v in params.items() if k not in {"limit", "offset"}}
    count_result = await db.execute(text(count_sql), count_params)
    total = int(count_result.scalar_one())

    return AdminUsageSnapshotListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


async def list_admin_stripe_events(
    db: AsyncSession,
    *,
    event_type: str | None = None,
    processed_after: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> AdminStripeEventListResponse:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    offset = (page - 1) * page_size

    where_clauses: list[str] = []
    params: dict[str, object] = {"limit": page_size, "offset": offset}

    if event_type:
        where_clauses.append("event_type = :event_type")
        params["event_type"] = event_type
    if processed_after is not None:
        where_clauses.append("processed_at >= :processed_after")
        params["processed_after"] = processed_after

    where_sql = ("where " + " and ".join(where_clauses)) if where_clauses else ""

    list_sql = f"""
        select id, stripe_event_id, event_type, processed_at
        from public.stripe_webhook_events
        {where_sql}
        order by processed_at desc, id asc
        limit :limit offset :offset
    """

    count_sql = f"""
        select count(*)::int as total
        from public.stripe_webhook_events
        {where_sql}
    """

    list_result = await db.execute(text(list_sql), params)
    items = [AdminStripeEventRow.model_validate(r) for r in list_result.mappings().all()]

    count_params = {k: v for k, v in params.items() if k not in {"limit", "offset"}}
    count_result = await db.execute(text(count_sql), count_params)
    total = int(count_result.scalar_one())

    return AdminStripeEventListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


__all__ = [
    "list_admin_subscriptions",
    "list_admin_usage_snapshots",
    "list_admin_stripe_events",
]
