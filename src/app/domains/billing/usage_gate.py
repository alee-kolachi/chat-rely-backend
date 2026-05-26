"""Runtime usage snapshot refresh; no conversation overage charges (see refresh_usage_period_snapshot)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.session import get_session_factory
from app.domains.plans.plan_limits import PlanModelPolicy, plan_model_policy_from_features
from app.domains.plans.subscription_queries import fetch_active_plan_model_policy

_usage_snapshot_memory: dict[str, tuple[float, UsageSnapshotSlice | None]] = {}
_plan_policy_memory: dict[str, tuple[float, PlanModelPolicy | None]] = {}


@dataclass(frozen=True)
class UsageSnapshotSlice:
    throttle_tier: str | None
    premium_turns_used: int
    included_premium_turns: int
    conversations_used: int
    included_conversations: int


def get_cached_usage_snapshot(user_id: UUID) -> UsageSnapshotSlice | None:
    """Last known usage slice for this user (avoids blocking chat on usage refresh)."""
    hit = _usage_snapshot_memory.get(str(user_id))
    if hit is None:
        return None
    ttl = float(get_settings().runtime_usage_tier_cache_ttl_seconds)
    if ttl > 0 and (time.monotonic() - hit[0]) < ttl:
        return hit[1]
    return None


def set_cached_usage_snapshot(user_id: UUID, snapshot: UsageSnapshotSlice | None) -> None:
    _usage_snapshot_memory[str(user_id)] = (time.monotonic(), snapshot)


def get_cached_usage_throttle_tier(user_id: UUID) -> str | None:
    snap = get_cached_usage_snapshot(user_id)
    return snap.throttle_tier if snap else None


def set_cached_usage_throttle_tier(user_id: UUID, tier: str | None) -> None:
    prev = get_cached_usage_snapshot(user_id)
    if prev is not None:
        set_cached_usage_snapshot(
            user_id,
            UsageSnapshotSlice(
                throttle_tier=tier,
                premium_turns_used=prev.premium_turns_used,
                included_premium_turns=prev.included_premium_turns,
                conversations_used=prev.conversations_used,
                included_conversations=prev.included_conversations,
            ),
        )
    elif tier is not None:
        set_cached_usage_snapshot(
            user_id,
            UsageSnapshotSlice(
                throttle_tier=tier,
                premium_turns_used=0,
                included_premium_turns=0,
                conversations_used=0,
                included_conversations=0,
            ),
        )


def get_cached_plan_model_policy(user_id: UUID) -> PlanModelPolicy | None:
    hit = _plan_policy_memory.get(str(user_id))
    if hit is None:
        return None
    ttl = float(get_settings().runtime_usage_tier_cache_ttl_seconds)
    if ttl > 0 and (time.monotonic() - hit[0]) < ttl:
        return hit[1]
    return None


def set_cached_plan_model_policy(user_id: UUID, policy: PlanModelPolicy | None) -> None:
    _plan_policy_memory[str(user_id)] = (time.monotonic(), policy)


async def fetch_plan_model_policy_cached(db: AsyncSession, user_id: UUID) -> PlanModelPolicy | None:
    cached = get_cached_plan_model_policy(user_id)
    if cached is not None:
        return cached
    policy = await fetch_active_plan_model_policy(db, user_id)
    set_cached_plan_model_policy(user_id, policy)
    return policy


async def refresh_plan_usage_snapshot(db: AsyncSession, user_id: UUID) -> UsageSnapshotSlice | None:
    """
    Refresh the usage snapshot for the user's current subscription period.

    Returns usage slice, or ``None`` if there is no active subscription.
    """
    sub = (
        await db.execute(
            text(
                """
                select
                  s.id as subscription_id,
                  s.current_period_start,
                  s.current_period_end
                from public.subscriptions s
                where s.user_id = cast(:uid as uuid)
                  and s.status in ('trialing', 'active', 'past_due')
                order by s.current_period_end desc
                limit 1
                """
            ),
            {"uid": str(user_id)},
        )
    ).mappings().first()
    if not sub:
        return None

    ps = sub["current_period_start"].astimezone(UTC).date()
    pe = sub["current_period_end"].astimezone(UTC).date()
    await db.execute(
        text(
            "select public.refresh_usage_period_snapshot(cast(:uid as uuid), cast(:ps as date), cast(:pe as date))"
        ),
        {"uid": str(user_id), "ps": ps, "pe": pe},
    )
    snap = (
        await db.execute(
            text(
                """
                select
                  throttle_tier::text as throttle_tier,
                  coalesce(premium_turns_used, 0) as premium_turns_used,
                  coalesce(included_premium_turns, 0) as included_premium_turns,
                  coalesce(conversations_used, 0) as conversations_used,
                  coalesce(included_conversations, 0) as included_conversations
                from public.usage_period_snapshots
                where user_id = cast(:uid as uuid)
                  and period_start = :ps
                  and period_end = :pe
                """
            ),
            {"uid": str(user_id), "ps": ps, "pe": pe},
        )
    ).mappings().first()
    if not snap:
        return None
    slice_ = UsageSnapshotSlice(
        throttle_tier=str(snap.get("throttle_tier") or "") or None,
        premium_turns_used=int(snap.get("premium_turns_used") or 0),
        included_premium_turns=int(snap.get("included_premium_turns") or 0),
        conversations_used=int(snap.get("conversations_used") or 0),
        included_conversations=int(snap.get("included_conversations") or 0),
    )
    set_cached_usage_snapshot(user_id, slice_)
    return slice_


async def refresh_plan_usage_snapshot_isolated(user_id: UUID) -> UsageSnapshotSlice | None:
    """Refresh usage on a dedicated session (safe to run concurrently with the request session)."""
    sf = get_session_factory()
    async with sf() as db:
        slice_ = await refresh_plan_usage_snapshot(db, user_id)
        policy = await fetch_active_plan_model_policy(db, user_id)
    set_cached_plan_model_policy(user_id, policy)
    return slice_


async def resolve_usage_snapshot_for_turn(
    user_id: UUID,
    *,
    refresh_task: asyncio.Task[UsageSnapshotSlice | None] | None = None,
) -> UsageSnapshotSlice | None:
    """Prefer a fresh usage refresh within a short budget; else use cached slice."""
    wait_s = float(get_settings().runtime_usage_refresh_wait_seconds)
    if refresh_task is not None and wait_s > 0:
        try:
            return await asyncio.wait_for(asyncio.shield(refresh_task), timeout=wait_s)
        except TimeoutError:
            pass
    elif refresh_task is not None and refresh_task.done():
        try:
            return refresh_task.result()
        except Exception:
            pass
    return get_cached_usage_snapshot(user_id)


async def resolve_usage_throttle_tier_for_turn(
    user_id: UUID,
    *,
    refresh_task: asyncio.Task[UsageSnapshotSlice | None] | None = None,
) -> str | None:
    snap = await resolve_usage_snapshot_for_turn(user_id, refresh_task=refresh_task)
    return snap.throttle_tier if snap else None


async def count_conversation_premium_turns(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    premium_model: str,
) -> int:
    row = (
        await db.execute(
            text(
                """
                select count(*)::int as n
                from public.messages m
                where m.conversation_id = cast(:cid as uuid)
                  and m.role = 'assistant'
                  and m.model = :model
                """
            ),
            {"cid": str(conversation_id), "model": premium_model},
        )
    ).mappings().first()
    return int(row["n"]) if row else 0
