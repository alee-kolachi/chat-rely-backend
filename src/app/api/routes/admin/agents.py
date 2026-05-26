from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_db, require_admin
from app.domains.admin.agents_service import (
    SortBy,
    SortDir,
    get_admin_agent_detail,
    list_admin_agents,
)
from app.domains.admin.schemas import AdminAgentDetail, AdminAgentListResponse


router = APIRouter()


@router.get("/agents", response_model=AdminAgentListResponse)
async def list_admin_agents_route(
    user_email: str | None = Query(default=None, description="ILIKE match on auth.users.email"),
    user_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None, description="active|paused|archived"),
    model: str | None = Query(default=None, description="Exact match on agents.model"),
    sort_by: Literal[
        "created_at",
        "name",
        "conversations_total",
        "conversations_mtd",
        "knowledge_sources_count",
    ] = Query(default="created_at"),
    sort_dir: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAgentListResponse:
    return await list_admin_agents(
        db,
        user_email=user_email,
        user_id=user_id,
        status=status,
        model=model,
        sort_by=_cast_sort_by(sort_by),
        sort_dir=_cast_sort_dir(sort_dir),
        page=page,
        page_size=page_size,
    )


@router.get("/agents/{agent_id}", response_model=AdminAgentDetail)
async def get_admin_agent_route(
    agent_id: UUID,
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAgentDetail:
    return await get_admin_agent_detail(db, agent_id)


def _cast_sort_by(value: str) -> SortBy:
    return value  # type: ignore[return-value]


def _cast_sort_dir(value: str) -> SortDir:
    return value  # type: ignore[return-value]
