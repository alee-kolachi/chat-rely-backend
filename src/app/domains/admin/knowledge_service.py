"""Cross-tenant reads for the admin Knowledge / Indexing surfaces.

Knowledge sources live behind an `agent_id` FK; we surface them as a flat list with
the owner email + agent name attached so admins don't have to drill in to identify
ownership. Indexing jobs share the same join shape because they always reference a
source.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.domains.admin.schemas import (
    AdminIndexingJobListResponse,
    AdminIndexingJobRow,
    AdminKnowledgeChunkPreview,
    AdminKnowledgeSourceDetail,
    AdminKnowledgeSourceListResponse,
    AdminKnowledgeSourceRow,
)


# Per-row preview length for chunk content. Chunks are typically much larger than this
# (~1k tokens / ~4k chars), so we truncate hard for the admin UI.
_CHUNK_PREVIEW_CHARS = 600


async def list_admin_knowledge_sources(
    db: AsyncSession,
    *,
    user_email: str | None = None,
    agent_id: UUID | None = None,
    type_: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> AdminKnowledgeSourceListResponse:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    offset = (page - 1) * page_size

    email_needle = (user_email or "").strip() or None
    where_clauses: list[str] = []
    params: dict[str, object] = {"limit": page_size, "offset": offset}

    if email_needle:
        where_clauses.append("u.email ilike '%' || :user_email || '%'")
        params["user_email"] = email_needle
    if agent_id is not None:
        where_clauses.append("ks.agent_id = :agent_id")
        params["agent_id"] = str(agent_id)
    if type_:
        where_clauses.append("ks.type = cast(:type as public.knowledge_source_type)")
        params["type"] = type_
    if status:
        where_clauses.append("ks.status = cast(:status as public.knowledge_source_status)")
        params["status"] = status

    where_sql = ("where " + " and ".join(where_clauses)) if where_clauses else ""

    list_sql = f"""
        select
          ks.id, ks.user_id, coalesce(u.email, '') as user_email,
          ks.agent_id, a.name as agent_name,
          ks.type::text as type, ks.title, ks.status::text as status,
          ks.source_url, ks.last_indexed_at, ks.error_message,
          coalesce(c.cnt, 0)::int as chunks_count,
          coalesce(c.tokens, 0)::int as chunks_total_tokens,
          ks.created_at
        from public.knowledge_sources ks
        join auth.users u on u.id = ks.user_id
        join public.agents a on a.id = ks.agent_id
        left join lateral (
          select count(*)::int as cnt, coalesce(sum(token_count), 0)::int as tokens
          from public.knowledge_chunks
          where knowledge_source_id = ks.id
        ) c on true
        {where_sql}
        order by ks.created_at desc, ks.id asc
        limit :limit offset :offset
    """

    count_sql = f"""
        select count(*)::int as total
        from public.knowledge_sources ks
        join auth.users u on u.id = ks.user_id
        join public.agents a on a.id = ks.agent_id
        {where_sql}
    """

    list_result = await db.execute(text(list_sql), params)
    items = [
        AdminKnowledgeSourceRow.model_validate(r) for r in list_result.mappings().all()
    ]

    count_params = {k: v for k, v in params.items() if k not in {"limit", "offset"}}
    count_result = await db.execute(text(count_sql), count_params)
    total = int(count_result.scalar_one())

    return AdminKnowledgeSourceListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


async def get_admin_knowledge_source_detail(
    db: AsyncSession,
    source_id: UUID,
) -> AdminKnowledgeSourceDetail:
    head = (
        await db.execute(
            text(
                """
                select
                  ks.id, ks.user_id, coalesce(u.email, '') as user_email,
                  ks.agent_id, a.name as agent_name,
                  ks.type::text as type, ks.title, ks.status::text as status,
                  ks.source_url, ks.last_indexed_at, ks.error_message,
                  ks.storage_bucket, ks.storage_path, ks.metadata,
                  coalesce(c.cnt, 0)::int as chunks_count,
                  coalesce(c.tokens, 0)::int as chunks_total_tokens,
                  ks.created_at
                from public.knowledge_sources ks
                join auth.users u on u.id = ks.user_id
                join public.agents a on a.id = ks.agent_id
                left join lateral (
                  select count(*)::int as cnt, coalesce(sum(token_count), 0)::int as tokens
                  from public.knowledge_chunks
                  where knowledge_source_id = ks.id
                ) c on true
                where ks.id = :source_id
                """
            ),
            {"source_id": str(source_id)},
        )
    ).mappings().first()
    if head is None:
        raise AppError(
            code="admin.knowledge_source_not_found",
            message="Knowledge source not found",
            status_code=404,
        )

    job_rows = (
        await db.execute(
            text(
                """
                select
                  ij.id, ij.user_id, coalesce(u.email, '') as user_email,
                  ij.agent_id, a.name as agent_name,
                  ij.knowledge_source_id, ks.title as knowledge_source_title,
                  ij.status::text as status, ij.attempt, ij.triggered_by,
                  ij.error_message, ij.started_at, ij.finished_at,
                  case
                    when ij.started_at is not null and ij.finished_at is not null
                      then (extract(epoch from (ij.finished_at - ij.started_at)) * 1000)::int
                    else null
                  end as duration_ms,
                  ij.created_at
                from public.indexing_jobs ij
                join auth.users u on u.id = ij.user_id
                join public.agents a on a.id = ij.agent_id
                join public.knowledge_sources ks on ks.id = ij.knowledge_source_id
                where ij.knowledge_source_id = :source_id
                order by ij.created_at desc
                limit 20
                """
            ),
            {"source_id": str(source_id)},
        )
    ).mappings().all()
    recent_jobs = [AdminIndexingJobRow.model_validate(r) for r in job_rows]

    chunk_rows = (
        await db.execute(
            text(
                f"""
                select
                  id, chunk_index, token_count,
                  left(content, {_CHUNK_PREVIEW_CHARS}) as content_preview,
                  created_at
                from public.knowledge_chunks
                where knowledge_source_id = :source_id
                order by chunk_index asc
                limit 5
                """
            ),
            {"source_id": str(source_id)},
        )
    ).mappings().all()
    sample_chunks = [
        AdminKnowledgeChunkPreview.model_validate(r) for r in chunk_rows
    ]

    return AdminKnowledgeSourceDetail(
        id=head["id"],
        user_id=head["user_id"],
        user_email=head["user_email"],
        agent_id=head["agent_id"],
        agent_name=head["agent_name"],
        type=head["type"],
        title=head["title"],
        status=head["status"],
        source_url=head["source_url"],
        last_indexed_at=head["last_indexed_at"],
        error_message=head["error_message"],
        chunks_count=int(head["chunks_count"] or 0),
        chunks_total_tokens=int(head["chunks_total_tokens"] or 0),
        created_at=head["created_at"],
        storage_bucket=head["storage_bucket"],
        storage_path=head["storage_path"],
        metadata=head["metadata"] or {},
        recent_jobs=recent_jobs,
        sample_chunks=sample_chunks,
    )


JobSortBy = Literal["created_at", "started_at", "finished_at", "status"]
JobSortDir = Literal["asc", "desc"]


_JOB_SORT_COLUMNS: dict[str, str] = {
    "created_at": "ij.created_at",
    "started_at": "ij.started_at",
    "finished_at": "ij.finished_at",
    "status": "ij.status",
}


def _resolve_job_order_by(sort_by: JobSortBy, sort_dir: JobSortDir) -> str:
    column = _JOB_SORT_COLUMNS.get(sort_by, _JOB_SORT_COLUMNS["created_at"])
    direction = "asc" if sort_dir == "asc" else "desc"
    return f"order by {column} {direction} nulls last, ij.id asc"


async def list_admin_indexing_jobs(
    db: AsyncSession,
    *,
    statuses: list[str] | None = None,
    user_email: str | None = None,
    agent_id: UUID | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    sort_by: JobSortBy = "created_at",
    sort_dir: JobSortDir = "desc",
    page: int = 1,
    page_size: int = 50,
) -> AdminIndexingJobListResponse:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    offset = (page - 1) * page_size

    email_needle = (user_email or "").strip() or None
    where_clauses: list[str] = []
    params: dict[str, object] = {"limit": page_size, "offset": offset}

    if statuses:
        # Cast to enum element-wise; expanding the list as bind params keeps it safe.
        placeholders = []
        for i, s in enumerate(statuses):
            key = f"status_{i}"
            placeholders.append(f"cast(:{key} as public.indexing_job_status)")
            params[key] = s
        where_clauses.append(f"ij.status in ({', '.join(placeholders)})")
    if email_needle:
        where_clauses.append("u.email ilike '%' || :user_email || '%'")
        params["user_email"] = email_needle
    if agent_id is not None:
        where_clauses.append("ij.agent_id = :agent_id")
        params["agent_id"] = str(agent_id)
    if started_after is not None:
        where_clauses.append("ij.started_at >= :started_after")
        params["started_after"] = started_after
    if started_before is not None:
        where_clauses.append("ij.started_at < :started_before")
        params["started_before"] = started_before

    where_sql = ("where " + " and ".join(where_clauses)) if where_clauses else ""

    list_sql = f"""
        select
          ij.id, ij.user_id, coalesce(u.email, '') as user_email,
          ij.agent_id, a.name as agent_name,
          ij.knowledge_source_id, ks.title as knowledge_source_title,
          ij.status::text as status, ij.attempt, ij.triggered_by,
          ij.error_message, ij.started_at, ij.finished_at,
          case
            when ij.started_at is not null and ij.finished_at is not null
              then (extract(epoch from (ij.finished_at - ij.started_at)) * 1000)::int
            else null
          end as duration_ms,
          ij.created_at
        from public.indexing_jobs ij
        join auth.users u on u.id = ij.user_id
        join public.agents a on a.id = ij.agent_id
        join public.knowledge_sources ks on ks.id = ij.knowledge_source_id
        {where_sql}
        {_resolve_job_order_by(sort_by, sort_dir)}
        limit :limit offset :offset
    """

    count_sql = f"""
        select count(*)::int as total
        from public.indexing_jobs ij
        join auth.users u on u.id = ij.user_id
        join public.agents a on a.id = ij.agent_id
        join public.knowledge_sources ks on ks.id = ij.knowledge_source_id
        {where_sql}
    """

    list_result = await db.execute(text(list_sql), params)
    items = [AdminIndexingJobRow.model_validate(r) for r in list_result.mappings().all()]

    count_params = {k: v for k, v in params.items() if k not in {"limit", "offset"}}
    count_result = await db.execute(text(count_sql), count_params)
    total = int(count_result.scalar_one())

    return AdminIndexingJobListResponse(
        items=items, total=total, page=page, page_size=page_size
    )
