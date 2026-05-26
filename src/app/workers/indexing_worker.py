import asyncio
import os
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text

from app.core.errors import AppError
from app.core.logging import setup_logging
from app.core.settings import get_settings
from app.db.engine import init_engine
from app.db.session import get_session_factory, init_session_factory
from app.domains.knowledge.service import process_indexing_job, record_worker_indexing_surrogate_failure

log = structlog.get_logger("indexing_worker")


async def _fetch_next_job_id() -> tuple[UUID, UUID] | None:
    async with get_session_factory()() as db:
        row = (
            await db.execute(
                text(
                    """
                    select id, user_id
                    from public.indexing_jobs
                    where status = 'queued'
                    order by created_at asc
                    limit 1
                    """
                )
            )
        ).mappings().first()
        if row is None:
            return None

        await db.execute(
            text(
                """
                update public.indexing_jobs
                set status = 'running',
                    phase = case
                      when phase::text = 'embedding_queued' then 'embedding'::public.indexing_job_phase
                      else 'crawling'::public.indexing_job_phase
                    end,
                    progress_pct = case
                      when phase::text = 'embedding_queued' then greatest(progress_pct, 26)
                      else greatest(progress_pct, 5)
                    end,
                    started_at = coalesce(started_at, now())
                where id = :job_id and status = 'queued'
                """
            ),
            {"job_id": str(row["id"])},
        )
        await db.commit()
        return UUID(str(row["id"])), UUID(str(row["user_id"]))


async def _persist_surrogate_safe(job_id: UUID, user_id: UUID, exc: BaseException) -> None:
    try:
        async with get_session_factory()() as db:
            await record_worker_indexing_surrogate_failure(db, job_id, user_id, exc)
    except Exception:
        log.exception(
            "indexing_job_surrogate_persist_failed",
            job_id=str(job_id),
            user_id=str(user_id),
        )


async def run_worker_loop(poll_interval_seconds: float = 2.0) -> None:
    # Worker only needs DB/OpenAI config; provide dev-safe auth defaults
    # so missing auth env vars do not block local worker startup.
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
    while True:
        fetched = await _fetch_next_job_id()
        if fetched is None:
            await asyncio.sleep(poll_interval_seconds)
            continue
        job_id, user_id = fetched
        try:
            async with get_session_factory()() as db:
                await process_indexing_job(db, job_id=job_id, user_id=user_id)
        except asyncio.CancelledError:
            # Ctrl+C / task cancellation during httpx or awaits — not a subclass of Exception,
            # so mark the job failed or it stays `running` forever.
            log.warning(
                "indexing_job_cancelled",
                job_id=str(job_id),
                user_id=str(user_id),
            )
            await _persist_surrogate_safe(
                job_id,
                user_id,
                RuntimeError("Indexing interrupted (worker cancelled during I/O)"),
            )
            raise
        except Exception as exc:
            if isinstance(exc, AppError):
                diag: dict[str, Any] = {
                    "job_id": str(job_id),
                    "user_id": str(user_id),
                    "error_code": exc.code,
                    "error_message": exc.message,
                }
                details = exc.details or {}
                if details:
                    diag["details"] = details
                    for key in (
                        "seed_url",
                        "knowledge_source_id",
                        "discovery_mode",
                        "urls_planned",
                        "urls_fetched",
                        "urls_empty_text",
                        "urls_with_text",
                        "urls_http_missing",
                        "urls_http_4xx",
                        "urls_http_5xx",
                        "crawl_stopped_reason",
                    ):
                        if key in details:
                            diag[key] = details[key]
                log.warning("indexing_job_failed", **diag)
            else:
                log.exception(
                    "indexing_job_failed",
                    job_id=str(job_id),
                    user_id=str(user_id),
                    error=str(exc),
                )
            await _persist_surrogate_safe(job_id, user_id, exc)


def main() -> None:
    try:
        asyncio.run(run_worker_loop())
    except KeyboardInterrupt:
        log.info("indexing_worker_stopped_by_user")


if __name__ == "__main__":
    main()
