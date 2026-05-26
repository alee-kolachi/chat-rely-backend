from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_db, require_admin
from app.domains.admin.schemas import AdminUserDetail, AdminUserListResponse
from app.domains.admin.users_service import (
    SortBy,
    SortDir,
    get_admin_user_detail,
    list_admin_users,
)


router = APIRouter()


@router.get("/users", response_model=AdminUserListResponse)
async def list_admin_users_route(
    q: str | None = Query(default=None, description="Search by email or full name (ILIKE)"),
    sort_by: Literal[
        "signed_up_at",
        "last_activity_at",
        "conversations_mtd",
        "email",
        "margin_mtd_usd",
        "total_cost_mtd_usd",
        "revenue_mtd_usd",
    ] = Query(default="signed_up_at"),
    sort_dir: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserListResponse:
    return await list_admin_users(
        db,
        search=q,
        sort_by=cast_sort_by(sort_by),
        sort_dir=cast_sort_dir(sort_dir),
        page=page,
        page_size=page_size,
    )


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_admin_user_route(
    user_id: UUID,
    _: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserDetail:
    return await get_admin_user_detail(db, user_id)


def cast_sort_by(value: str) -> SortBy:
    """Narrow runtime str (validated by FastAPI Literal) to the typed alias."""
    return value  # type: ignore[return-value]


def cast_sort_dir(value: str) -> SortDir:
    return value  # type: ignore[return-value]
