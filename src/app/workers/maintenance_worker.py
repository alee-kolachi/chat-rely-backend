"""Periodic idle conversation closure, outcome backfill, and usage snapshot refresh."""

from __future__ import annotations

import asyncio
import os

import structlog
from sqlalchemy import text

from app.core.logging import setup_logging
from app.core.settings import get_settings
from app.db.engine import init_engine
from app.db.session import get_session_factory, init_session_factory
from app.domains.conversation_outcomes.service import tick_idle_and_outcomes

log = structlog.get_logger("maintenance_worker")


async def refresh_all_usage_snapshots() -> int:
    """Refresh usage_period_snapshots for every workspace with an active subscription."""
    async with get_session_factory()() as db:
        rows = (
            await db.execute(
                text(
                    """
                    select distinct on (user_id)
                      user_id,
                      current_period_start::date as ps,
                      current_period_end::date as pe
                    from public.subscriptions
                    where status in ('trialing', 'active', 'past_due')
                    order by user_id, current_period_end desc
                    """
                )
            )
        ).mappings().all()

        n = 0
        for row in rows:
            uid = row["user_id"]
            ps = row["ps"]
            pe = row["pe"]
            await db.execute(
                text(
                    "select public.refresh_usage_period_snapshot(cast(:uid as uuid), cast(:ps as date), cast(:pe as date))"
                ),
                {"uid": str(uid), "ps": ps, "pe": pe},
            )
            n += 1
        await db.commit()
        return n


async def run_tick_once() -> tuple[int, int, int]:
    """Returns (idle_closed_count, outcomes_processed, usage_rows_refreshed)."""
    async with get_session_factory()() as db:
        idle_n, outcome_n = await tick_idle_and_outcomes(db)
        await db.commit()
    usage_n = await refresh_all_usage_snapshots()
    return idle_n, outcome_n, usage_n


async def run_loop(interval_seconds: float = 60.0) -> None:
    os.environ.setdefault("SUPABASE_JWKS_URL", "https://example.com/.well-known/jwks.json")
    os.environ.setdefault("SUPABASE_ISSUER", "https://example.com/auth/v1")
    settings = get_settings()
    setup_logging(
        settings.log_level,
        log_file_enabled=settings.log_file_enabled,
        log_file_path=settings.log_file_path,
        log_file_max_bytes=settings.log_file_max_bytes,
        log_file_backup_count=settings.log_file_backup_count,
        log_pretty_file_enabled=settings.log_pretty_file_enabled,
        log_pretty_file_path=settings.log_pretty_file_path,
        log_pretty_file_max_bytes=settings.log_pretty_file_max_bytes,
        log_pretty_file_backup_count=settings.log_pretty_file_backup_count,
    )
    init_engine(settings)
    init_session_factory()
    log.info("maintenance_worker_started", interval_seconds=interval_seconds)
    while True:
        try:
            idle_n, outcome_n, usage_n = await run_tick_once()
            log.info(
                "maintenance_tick",
                idle_closed=idle_n,
                outcomes=outcome_n,
                usage_refreshed=usage_n,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("maintenance_tick_failed")
        await asyncio.sleep(interval_seconds)


def main() -> None:
    try:
        asyncio.run(run_loop())
    except KeyboardInterrupt:
        log.info("maintenance_worker_stopped")


if __name__ == "__main__":
    main()
