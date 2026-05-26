from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_db, require_admin
from app.domains.admin.knowledge_service import (
    JobSortBy,
    JobSortDir,
    get_admin_knowledge_source_detail,
    list_admin_indexing_jobs,
    list_admin_knowledge_sources,
)
from app.domains.admin.schemas import (
    AdminIndexingJobListResponse,
    AdminKnowledgeSourceDetail,
    AdminKnowledgeSourceListResponse,
)


router = APIRouter()


@router.get("/knowledge/sources", response_model=AdminKnowledgeSourceListResponse)
async def list_admin_knowledge_sources_route(
    user_email: str | None = Query(default=None, description="ILIKE match on auth.users.email"),
    agent_id: UUID | None = Query(default=None),
    type: str | None = Query(default=None, description="website|file|text_snippet|q_and_a"),
    status: str | None = Query(default=None, description="pending|indexing|ready|failed"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminKnowledgeSourceListResponse:
    return await list_admin_knowledge_sources(
        db,
        user_email=user_email,
        agent_id=agent_id,
        type_=type,
        status=status,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/knowledge/sources/{source_id}", response_model=AdminKnowledgeSourceDetail
)
async def get_admin_knowledge_source_route(
    source_id: UUID,
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminKnowledgeSourceDetail:
    return await get_admin_knowledge_source_detail(db, source_id)


@router.get("/indexing/jobs", response_model=AdminIndexingJobListResponse)
async def list_admin_indexing_jobs_route(
    status: list[str] | None = Query(
        default=None,
        description="Repeat ?status=queued&status=running&status=failed for multi-select.",
    ),
    user_email: str | None = Query(default=None),
    agent_id: UUID | None = Query(default=None),
    started_after: datetime | None = Query(default=None),
    started_before: datetime | None = Query(default=None),
    sort_by: Literal["created_at", "started_at", "finished_at", "status"] = Query(
        default="created_at"
    ),
    sort_dir: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminIndexingJobListResponse:
    return await list_admin_indexing_jobs(
        db,
        statuses=status,
        user_email=user_email,
        agent_id=agent_id,
        started_after=started_after,
        started_before=started_before,
        sort_by=_cast_job_sort_by(sort_by),
        sort_dir=_cast_job_sort_dir(sort_dir),
        page=page,
        page_size=page_size,
    )


def _cast_job_sort_by(value: str) -> JobSortBy:
    return value  # type: ignore[return-value]


def _cast_job_sort_dir(value: str) -> JobSortDir:
    return value  # type: ignore[return-value]
