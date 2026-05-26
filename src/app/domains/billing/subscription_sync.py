"""Apply Stripe subscription state to public.subscriptions (user workspace)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import stripe
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.settings import get_settings
from app.domains.billing.price_map import canonical_plan_slug, slug_for_price_id
from app.domains.billing.stripe_client import configure_stripe

log = structlog.get_logger(__name__)


def _stripe_obj_to_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    fn = getattr(obj, "to_dict", None)
    if callable(fn):
        return fn()
    return {}


def _coerce_unix_ts(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_subscription_period_bounds(stripe_sub: dict[str, Any]) -> tuple[datetime, datetime]:
    """Return billing period bounds from either subscription or subscription item payload."""
    start = _coerce_unix_ts(stripe_sub.get("current_period_start"))
    end = _coerce_unix_ts(stripe_sub.get("current_period_end"))

    if start is None or end is None:
        items = stripe_sub.get("items") or {}
        data = items.get("data") if isinstance(items, dict) else None
        first = data[0] if isinstance(data, list) and data else {}
        if isinstance(first, dict):
            start = start or _coerce_unix_ts(first.get("current_period_start"))
            end = end or _coerce_unix_ts(first.get("current_period_end"))

    if start is None:
        start = int(datetime.now(tz=UTC).timestamp())
    if end is None:
        end = int((datetime.fromtimestamp(start, tz=UTC) + timedelta(days=31)).timestamp())
    if end <= start:
        end = int((datetime.fromtimestamp(start, tz=UTC) + timedelta(days=1)).timestamp())

    return datetime.fromtimestamp(start, tz=UTC), datetime.fromtimestamp(end, tz=UTC)


def _uuid_from_metadata(meta: dict[str, Any] | None, key: str) -> UUID | None:
    if not meta:
        return None
    raw = meta.get(key)
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


def user_id_from_checkout_session_payload(data: dict[str, Any]) -> UUID | None:
    meta = data.get("metadata") or {}
    uid = _uuid_from_metadata(meta if isinstance(meta, dict) else {}, "user_id")
    if uid is None and data.get("client_reference_id"):
        try:
            uid = UUID(str(data["client_reference_id"]))
        except ValueError:
            return None
    return uid


def _stripe_id_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("id") or "").strip()
    return str(value).strip()


async def sync_subscription_from_checkout_session_payload(db: AsyncSession, data: dict[str, Any]) -> bool:
    """
    Apply a checkout.session payload (subscription mode) to public.subscriptions.

    Used by Stripe webhooks and by the authenticated finalize-checkout endpoint so local/dev
    works when webhooks cannot reach localhost.
    """
    mode = data.get("mode")
    if mode != "subscription":
        return False

    sub_id = _stripe_id_field(data.get("subscription"))
    cust_id = _stripe_id_field(data.get("customer"))
    if not sub_id or not cust_id:
        log.warning("stripe.checkout.missing_sub_or_customer", session_id=data.get("id"))
        return False

    user_id = user_id_from_checkout_session_payload(data)
    if user_id is None:
        log.warning("stripe.checkout.missing_user", session_id=data.get("id"))
        return False

    stripe_sub = await fetch_stripe_subscription(sub_id)
    plan_id = await resolve_plan_id_for_stripe_subscription(db, stripe_sub)
    if plan_id is None:
        meta_raw = data.get("metadata") or {}
        meta = meta_raw if isinstance(meta_raw, dict) else {}
        slug = canonical_plan_slug(str(meta.get("plan_slug") or ""))
        if slug:
            r = await db.execute(
                text("select id from public.plans where slug = :slug and is_active = true limit 1"),
                {"slug": slug},
            )
            row = r.mappings().first()
            if row:
                plan_id = UUID(str(row["id"]))
    if plan_id is None:
        log.error("stripe.checkout.plan_unresolved", user_id=str(user_id), subscription_id=sub_id)
        return False

    stripe_sub_dict = _stripe_obj_to_dict(stripe_sub)
    cps, cpe = _extract_subscription_period_bounds(stripe_sub_dict)
    status = str(stripe_sub_dict.get("status") or "active")
    cape = bool(stripe_sub_dict.get("cancel_at_period_end"))

    await upsert_user_subscription_from_stripe(
        db,
        user_id=user_id,
        stripe_customer_id=cust_id,
        stripe_subscription_id=sub_id,
        plan_id=plan_id,
        status=status,
        current_period_start=cps,
        current_period_end=cpe,
        cancel_at_period_end=cape,
    )
    return True


def _month_period(now: datetime) -> tuple[datetime, datetime]:
    start = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month_seed = start.replace(day=28) + timedelta(days=4)
    end = next_month_seed.replace(day=1) - timedelta(microseconds=1)
    return start, end


def stripe_subscription_status_to_db(stripe_status: str | None) -> str:
    s = (stripe_status or "").strip().lower()
    mapping = {
        "active": "active",
        "trialing": "trialing",
        "past_due": "past_due",
        "canceled": "canceled",
        "unpaid": "unpaid",
        "incomplete": "incomplete",
        "incomplete_expired": "incomplete",
        "paused": "active",
    }
    return mapping.get(s, "active")


def _price_id_from_subscription_item(item: dict[str, Any] | Any) -> str | None:
    if item is None:
        return None
    price = getattr(item, "price", None) if not isinstance(item, dict) else item.get("price")
    if price is None:
        return None
    pid = getattr(price, "id", None)
    if pid:
        return str(pid)
    if isinstance(price, dict):
        return str(price.get("id") or "")
    return None


async def resolve_plan_id_for_stripe_subscription(db: AsyncSession, stripe_sub: Any) -> UUID | None:
    """Map first subscription item price to local plan id."""
    stripe_sub_dict = _stripe_obj_to_dict(stripe_sub)
    items_obj = stripe_sub_dict.get("items") or {}
    data = items_obj.get("data") if isinstance(items_obj, dict) else []
    if not data:
        return None
    first = data[0]
    pid = _price_id_from_subscription_item(first)
    if not pid:
        return None
    slug = slug_for_price_id(get_settings(), pid)
    if not slug:
        return None
    res = await db.execute(
        text("select id from public.plans where slug = :slug and is_active = true limit 1"),
        {"slug": slug},
    )
    row = res.mappings().first()
    return UUID(str(row["id"])) if row else None


async def free_plan_id(db: AsyncSession) -> UUID:
    res = await db.execute(
        text("select id from public.plans where slug = 'free' and is_active = true limit 1"),
    )
    row = res.mappings().first()
    if not row:
        raise AppError(code="plan.not_found", message="No active free plan in database", status_code=500)
    return UUID(str(row["id"]))


async def upsert_user_subscription_from_stripe(
    db: AsyncSession,
    *,
    user_id: UUID,
    stripe_customer_id: str,
    stripe_subscription_id: str,
    plan_id: UUID,
    status: str,
    current_period_start: datetime,
    current_period_end: datetime,
    cancel_at_period_end: bool,
) -> None:
    db_status = stripe_subscription_status_to_db(status)
    result = await db.execute(
        text(
            """
            update public.subscriptions s
            set
              provider_customer_id = :cust,
              provider_subscription_id = :sub,
              plan_id = cast(:plan as uuid),
              status = cast(:st as subscription_status),
              current_period_start = :cps,
              current_period_end = :cpe,
              cancel_at_period_end = :cape,
              updated_at = now()
            from (
              select id from public.subscriptions
              where user_id = cast(:uid as uuid)
              order by created_at desc
              limit 1
            ) pick
            where s.id = pick.id
            """
        ),
        {
            "cust": stripe_customer_id,
            "sub": stripe_subscription_id,
            "plan": str(plan_id),
            "st": db_status,
            "cps": current_period_start,
            "cpe": current_period_end,
            "cape": cancel_at_period_end,
            "uid": str(user_id),
        },
    )
    if (result.rowcount or 0) > 0:
        return

    await db.execute(
        text(
            """
            insert into public.subscriptions (
              user_id,
              plan_id,
              status,
              current_period_start,
              current_period_end,
              cancel_at_period_end,
              provider_customer_id,
              provider_subscription_id
            ) values (
              cast(:uid as uuid),
              cast(:plan as uuid),
              cast(:st as subscription_status),
              :cps,
              :cpe,
              :cape,
              :cust,
              :sub
            )
            """
        ),
        {
            "uid": str(user_id),
            "plan": str(plan_id),
            "st": db_status,
            "cps": current_period_start,
            "cpe": current_period_end,
            "cape": cancel_at_period_end,
            "cust": stripe_customer_id,
            "sub": stripe_subscription_id,
        },
    )


_ACTIVE_STRIPE_STATUSES = frozenset({"active", "trialing", "past_due"})


async def move_user_to_free_after_stripe_subscription_deleted(
    db: AsyncSession,
    *,
    user_id: UUID,
    deleted_stripe_subscription_id: str,
) -> None:
    fid = await free_plan_id(db)
    ps, pe = _month_period(datetime.now(tz=UTC))
    meta = {"previous_stripe_subscription_id": deleted_stripe_subscription_id}

    result = await db.execute(
        text(
            """
            update public.subscriptions s
            set
              plan_id = cast(:fid as uuid),
              status = 'active'::subscription_status,
              provider_subscription_id = null,
              cancel_at_period_end = false,
              current_period_start = :ps,
              current_period_end = :pe,
              metadata = coalesce(s.metadata, '{}'::jsonb) || cast(:meta as jsonb),
              updated_at = now()
            where s.user_id = cast(:uid as uuid)
              and s.provider_subscription_id = :sid
            """
        ),
        {
            "fid": str(fid),
            "ps": ps,
            "pe": pe,
            "meta": json.dumps(meta),
            "uid": str(user_id),
            "sid": deleted_stripe_subscription_id,
        },
    )
    if (result.rowcount or 0) > 0:
        return

    await db.execute(
        text(
            """
            update public.subscriptions s
            set
              plan_id = cast(:fid as uuid),
              status = 'active'::subscription_status,
              provider_subscription_id = null,
              cancel_at_period_end = false,
              current_period_start = :ps,
              current_period_end = :pe,
              metadata = coalesce(s.metadata, '{}'::jsonb) || cast(:meta as jsonb),
              updated_at = now()
            from (
              select id from public.subscriptions
              where user_id = cast(:uid as uuid)
              order by created_at desc
              limit 1
            ) pick
            where s.id = pick.id
            """
        ),
        {
            "fid": str(fid),
            "ps": ps,
            "pe": pe,
            "meta": json.dumps(meta),
            "uid": str(user_id),
        },
    )


async def sync_user_subscription_from_stripe(db: AsyncSession, *, user_id: UUID) -> dict[str, str]:
    """
    Pull the latest Stripe subscription for this workspace into public.subscriptions.

    Used after Customer Portal return and as a webhook fallback (e.g. localhost).
    """
    configure_stripe()
    settings = get_settings()
    if not (settings.stripe_secret_key or "").strip():
        raise AppError(
            code="stripe.not_configured",
            message="Stripe is not configured",
            status_code=503,
        )

    from app.domains.billing.customers import ensure_stripe_customer_for_user, fetch_auth_user_email

    row = (
        await db.execute(
            text(
                """
                select provider_customer_id, provider_subscription_id
                from public.subscriptions
                where user_id = cast(:uid as uuid)
                order by created_at desc
                limit 1
                """
            ),
            {"uid": str(user_id)},
        )
    ).mappings().first()

    customer_id = (
        str(row["provider_customer_id"]).strip()
        if row and row.get("provider_customer_id")
        else ""
    )
    stored_sub_id = (
        str(row["provider_subscription_id"]).strip()
        if row and row.get("provider_subscription_id")
        else ""
    )

    if not customer_id:
        email = await fetch_auth_user_email(db, user_id)
        customer_id = await ensure_stripe_customer_for_user(db, user_id=user_id, email=email) or ""

    best_sub: dict[str, Any] | None = None

    if customer_id:
        try:
            listed = stripe.Subscription.list(
                customer=customer_id,
                status="all",
                limit=20,
                expand=["data.items.data.price"],
            )
        except stripe.StripeError as exc:
            raise AppError(
                code="stripe.subscription_list_failed",
                message="Could not list subscriptions from Stripe",
                status_code=502,
                details={"stripe": str(exc)[:400]},
            ) from exc

        data = listed.get("data") if isinstance(listed, dict) else getattr(listed, "data", None)
        for sub in data or []:
            sub_dict = _stripe_obj_to_dict(sub)
            st = str(sub_dict.get("status") or "").strip().lower()
            if st in _ACTIVE_STRIPE_STATUSES:
                best_sub = sub_dict
                break

    if best_sub is None and stored_sub_id:
        try:
            retrieved = await fetch_stripe_subscription(stored_sub_id)
            sub_dict = _stripe_obj_to_dict(retrieved)
            st = str(sub_dict.get("status") or "").strip().lower()
            if st in _ACTIVE_STRIPE_STATUSES:
                best_sub = sub_dict
            elif st == "canceled":
                await move_user_to_free_after_stripe_subscription_deleted(
                    db, user_id=user_id, deleted_stripe_subscription_id=stored_sub_id
                )
                return {"outcome": "downgraded_to_free", "reason": "subscription_canceled"}
        except stripe.InvalidRequestError:
            await move_user_to_free_after_stripe_subscription_deleted(
                db, user_id=user_id, deleted_stripe_subscription_id=stored_sub_id
            )
            return {"outcome": "downgraded_to_free", "reason": "subscription_not_found"}

    if best_sub is None:
        if stored_sub_id:
            await move_user_to_free_after_stripe_subscription_deleted(
                db, user_id=user_id, deleted_stripe_subscription_id=stored_sub_id
            )
            return {"outcome": "downgraded_to_free", "reason": "no_active_subscription"}
        return {"outcome": "unchanged", "reason": "no_stripe_subscription"}

    sub_id = str(best_sub.get("id") or "").strip()
    cust_id = _stripe_id_field(best_sub.get("customer")) or customer_id
    meta = best_sub.get("metadata") if isinstance(best_sub.get("metadata"), dict) else {}
    plan_id = await resolve_plan_id_for_stripe_subscription(db, best_sub)
    if plan_id is None:
        slug = canonical_plan_slug(str(meta.get("plan_slug") or ""))
        if slug:
            r = await db.execute(
                text("select id from public.plans where slug = :slug and is_active = true limit 1"),
                {"slug": slug},
            )
            prow = r.mappings().first()
            if prow:
                plan_id = UUID(str(prow["id"]))
    if plan_id is None:
        raise AppError(
            code="billing.plan_unresolved",
            message="Could not map Stripe subscription to a plan",
            status_code=502,
        )

    cps, cpe = _extract_subscription_period_bounds(best_sub)
    status = str(best_sub.get("status") or "active").strip().lower()
    cape = bool(best_sub.get("cancel_at_period_end"))

    # User canceled in Stripe (immediate or at period end) — workspace should be on Free.
    if cape or status == "canceled":
        await move_user_to_free_after_stripe_subscription_deleted(
            db, user_id=user_id, deleted_stripe_subscription_id=sub_id
        )
        return {
            "outcome": "downgraded_to_free",
            "reason": "subscription_canceled" if status == "canceled" else "cancellation_scheduled",
        }

    await upsert_user_subscription_from_stripe(
        db,
        user_id=user_id,
        stripe_customer_id=cust_id,
        stripe_subscription_id=sub_id,
        plan_id=plan_id,
        status=status,
        current_period_start=cps,
        current_period_end=cpe,
        cancel_at_period_end=cape,
    )
    return {
        "outcome": "synced",
        "status": status,
        "cancel_at_period_end": "true" if cape else "false",
    }


async def fetch_stripe_subscription(subscription_id: str) -> Any:
    configure_stripe()
    return stripe.Subscription.retrieve(subscription_id, expand=["items.data.price"])
