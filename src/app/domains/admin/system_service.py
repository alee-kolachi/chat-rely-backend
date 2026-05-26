"""System health endpoint for the admin panel.

Wraps:
- App metadata (name, version, env) from `Settings`.
- A `select 1` for DB readiness + latency.
- Worker heartbeats proxied from `indexing_jobs` and `usage_period_snapshots`.
- Pricing status: which models have prices in env, plus any model that *appears* in
  `messages.model` but is missing from the price maps.
"""

from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.domains.admin.costing import (
    embedding_pricing_status,
    unknown_models_warning,
)
from app.domains.admin.schemas import (
    AdminPricingStatus,
    AdminSystemHealth,
    AdminWorkerStatus,
)


async def get_admin_system_health(
    db: AsyncSession,
    *,
    settings: Settings | None = None,
) -> AdminSystemHealth:
    settings = settings or get_settings()

    # Database probe: time a trivial round-trip. Catch broad exceptions so a degraded DB
    # doesn't bring down the System page entirely.
    db_ready = True
    started = time.perf_counter()
    try:
        await db.execute(text("select 1"))
    except Exception:  # noqa: BLE001 — admin probe; we report failure rather than re-raise.
        db_ready = False
    db_latency_ms = int((time.perf_counter() - started) * 1000)

    workers = AdminWorkerStatus()
    if db_ready:
        worker_row = (
            await db.execute(
                text(
                    """
                    select
                      (
                        select max(finished_at)
                        from public.indexing_jobs
                        where status = 'succeeded'
                      ) as indexing_last_success_at,
                      (
                        select max(coalesce(started_at, created_at))
                        from public.indexing_jobs
                      ) as indexing_last_attempt_at,
                      (
                        select count(*)::int
                        from public.indexing_jobs
                        where status in ('queued', 'running')
                      ) as indexing_queue_depth,
                      (
                        select max(last_computed_at)
                        from public.usage_period_snapshots
                      ) as maintenance_last_computed_at
                    """
                )
            )
        ).mappings().one()
        workers = AdminWorkerStatus(
            indexing_last_success_at=worker_row["indexing_last_success_at"],
            indexing_last_attempt_at=worker_row["indexing_last_attempt_at"],
            indexing_queue_depth=int(worker_row["indexing_queue_depth"] or 0),
            maintenance_last_computed_at=worker_row["maintenance_last_computed_at"],
        )

    # Pricing status — distinct models seen in any message + diff vs configured price maps.
    unknown_models: list[str] = []
    if db_ready:
        observed = (
            await db.execute(
                text(
                    """
                    select distinct m.model
                    from public.messages m
                    where m.model is not null
                    """
                )
            )
        ).scalars().all()
        unknown_models = unknown_models_warning(observed, settings)

    embed_priced, embed_model = embedding_pricing_status(settings)

    pricing = AdminPricingStatus(
        llm_input_models=sorted(settings.llm_input_price_per_million_usd.keys()),
        llm_output_models=sorted(settings.llm_output_price_per_million_usd.keys()),
        embedding_models=sorted(settings.embedding_price_per_million_usd.keys()),
        embedding_active_model=embed_model,
        embedding_active_model_priced=embed_priced,
        unknown_models_in_messages=unknown_models,
    )

    return AdminSystemHealth(
        app_name=settings.app_name,
        app_version=settings.app_version,
        app_env=settings.app_env,
        database_ready=db_ready,
        database_latency_ms=db_latency_ms,
        workers=workers,
        pricing_configured=pricing,
    )
