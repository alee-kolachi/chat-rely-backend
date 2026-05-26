from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.bootstrap.service import ensure_user_profile
from app.domains.notifications.schemas import NotificationDTO

log = structlog.get_logger(__name__)


async def create_notification(
    db: AsyncSession,
    *,
    user_id: UUID,
    kind: str,
    title: str,
    body: str,
    href: str,
    metadata: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> UUID | None:
    """Insert a notification row. Returns id when inserted, None on dedupe conflict or empty insert."""
    md = metadata if metadata is not None else {}
    params: dict[str, Any] = {
        "user_id": str(user_id),
        "kind": kind,
        "title": title,
        "body": body,
        "href": href,
        "metadata": json.dumps(md, default=str),
        "dedupe_key": dedupe_key,
    }
    if dedupe_key:
        result = await db.execute(
            text(
                """
                insert into public.user_notifications (
                  user_id, kind, title, body, href, metadata, dedupe_key
                ) values (
                  cast(:user_id as uuid), :kind, :title, :body, :href,
                  cast(:metadata as jsonb), :dedupe_key
                )
                on conflict (user_id, dedupe_key) where dedupe_key is not null do nothing
                returning id
                """
            ),
            params,
        )
    else:
        result = await db.execute(
            text(
                """
                insert into public.user_notifications (
                  user_id, kind, title, body, href, metadata
                ) values (
                  cast(:user_id as uuid), :kind, :title, :body, :href,
                  cast(:metadata as jsonb)
                )
                returning id
                """
            ),
            {
                "user_id": str(user_id),
                "kind": kind,
                "title": title,
                "body": body,
                "href": href,
                "metadata": json.dumps(md, default=str),
            },
        )
    row = result.first()
    if row is None:
        return None
    return UUID(str(row[0]))


async def create_notification_best_effort(
    db: AsyncSession,
    *,
    user_id: UUID,
    kind: str,
    title: str,
    body: str,
    href: str,
    metadata: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> None:
    try:
        await ensure_user_profile(db, user_id)
        nid = await create_notification(
            db,
            user_id=user_id,
            kind=kind,
            title=title,
            body=body,
            href=href,
            metadata=metadata,
            dedupe_key=dedupe_key,
        )
        if nid is not None:
            await db.commit()
    except Exception:
        await db.rollback()
        log.warning(
            "notifications.insert_failed",
            user_id=str(user_id),
            kind=kind,
            exc_info=True,
        )


async def list_notifications(
    db: AsyncSession,
    *,
    user_id: UUID,
    limit: int = 50,
) -> tuple[list[NotificationDTO], int]:
    lim = max(1, min(limit, 100))
    rows = (
        await db.execute(
            text(
                """
                select id, kind, title, body, href, metadata, read_at, created_at
                from public.user_notifications
                where user_id = cast(:uid as uuid)
                order by created_at desc
                limit :lim
                """
            ),
            {"uid": str(user_id), "lim": lim},
        )
    ).mappings().all()

    unread = (
        await db.execute(
            text(
                """
                select count(*)::int as c
                from public.user_notifications
                where user_id = cast(:uid as uuid) and read_at is null
                """
            ),
            {"uid": str(user_id)},
        )
    ).scalar_one()

    items = [
        NotificationDTO(
            id=r["id"],
            kind=r["kind"],
            title=r["title"],
            body=r["body"],
            href=r["href"],
            metadata=dict(r["metadata"]) if isinstance(r["metadata"], dict) else {},
            read_at=r["read_at"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
    return items, int(unread)


async def mark_notifications_read(
    db: AsyncSession,
    *,
    user_id: UUID,
    notification_ids: list[UUID] | None,
    mark_all: bool,
) -> int:
    if mark_all:
        result = await db.execute(
            text(
                """
                update public.user_notifications
                set read_at = now()
                where user_id = cast(:uid as uuid) and read_at is null
                """
            ),
            {"uid": str(user_id)},
        )
        await db.commit()
        return result.rowcount or 0

    if not notification_ids:
        return 0

    stmt = text(
        """
        update public.user_notifications
        set read_at = now()
        where user_id = cast(:uid as uuid)
          and read_at is null
          and id in :ids
        """
    ).bindparams(bindparam("ids", expanding=True))
    result = await db.execute(
        stmt,
        {"uid": str(user_id), "ids": notification_ids},
    )
    await db.commit()
    return result.rowcount or 0


async def maybe_emit_usage_warning_notification(
    db: AsyncSession,
    *,
    user_id: UUID,
    period_start: Any,
    period_end: Any,
) -> None:
    """If conversations_used is >= 80% and below 100% of included conversations, insert once per period."""
    from app.domains.notifications.links import href_usage

    row = (
        await db.execute(
            text(
                """
                select conversations_used, included_conversations
                from public.usage_period_snapshots
                where user_id = cast(:uid as uuid)
                  and period_start = :ps
                  and period_end = :pe
                """
            ),
            {"uid": str(user_id), "ps": period_start, "pe": period_end},
        )
    ).mappings().first()
    if row is None:
        return

    included = int(row.get("included_conversations") or 0)
    used = int(row.get("conversations_used") or 0)
    if included <= 0:
        return
    ratio = used / float(included)
    if ratio < 0.8 or ratio >= 1.0:
        return

    dedupe = f"usage_warn_80:{period_start}:{period_end}"
    pct = int(round(ratio * 100))
    await create_notification_best_effort(
        db,
        user_id=user_id,
        kind="usage_warning_80",
        title="Usage reached 80%",
        body=f"You have used about {pct}% of included conversations this billing period ({used} of {included}).",
        href=href_usage(),
        metadata={"conversations_used": used, "included": included, "period_start": str(period_start), "period_end": str(period_end)},
        dedupe_key=dedupe,
    )
