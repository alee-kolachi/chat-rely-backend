from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.domains.conversation_outcomes.service import tick_idle_and_outcomes
from app.domains.dashboard.schemas import (
    AgentDashboardResponse,
    DashboardRecentRow,
    DashboardSeriesPoint,
    TrainingTopicSummary,
)
from app.domains.plans.plan_limits import sources_suggestions_enabled_for_plan_slug
from app.domains.plans.subscription_queries import fetch_active_plan_slug

log = structlog.get_logger("dashboard")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def resolve_dashboard_range(
    *,
    range_key: str | None,
    range_from: datetime | None,
    range_to: datetime | None,
) -> tuple[datetime, datetime]:
    now = _utc_now()
    if range_from is not None and range_to is not None:
        return range_from, range_to
    presets = {
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "90d": timedelta(days=90),
        "365d": timedelta(days=365),
    }
    default_delta = presets["30d"]
    key = (range_key or "30d").strip().lower()
    delta = presets.get(key, default_delta)
    start = now - delta
    return start, now


async def build_agent_dashboard(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    range_key: str | None,
    range_from: datetime | None,
    range_to: datetime | None,
    tick_lifecycle: bool = True,
) -> AgentDashboardResponse:
    request_start = perf_counter()
    agent_lookup_ms = 0.0
    rf, rt = resolve_dashboard_range(
        range_key=range_key, range_from=range_from, range_to=range_to
    )

    lifecycle_ms = 0.0
    lifecycle_idle_n = 0
    lifecycle_outcome_n = 0
    if tick_lifecycle:
        step_start = perf_counter()
        lifecycle_idle_n, lifecycle_outcome_n = await tick_idle_and_outcomes(db)
        lifecycle_ms = round((perf_counter() - step_start) * 1000, 1)

    agent_s = str(agent_id)
    user_s = str(user_id)
    params: dict[str, object] = {
        "agent_id": agent_s,
        "user_id": user_s,
        "rf": rf,
        "rt": rt,
    }

    step_start = perf_counter()
    summary = await db.execute(
        text(
            """
            with agent_ok as (
              select exists(
                select 1
                from public.agents a
                where a.id = cast(:agent_id as uuid)
                  and a.user_id = cast(:user_id as uuid)
              ) as ok
            ),
            convo_all as (
              select
                c.id,
                c.user_id,
                c.visitor_id,
                c.status,
                c.started_at,
                c.last_activity_at,
                c.counts_toward_plan
              from public.conversations c
              where c.user_id = cast(:user_id as uuid)
                and c.agent_id = cast(:agent_id as uuid)
                and (select ok from agent_ok)
            ),
            convo_range as (
              select *
              from convo_all
              where started_at >= :rf
                and started_at < :rt
            ),
            outcomes as (
              select
                o.payload,
                cr.id as conversation_id
              from public.conversation_outcomes o
              join convo_range cr on cr.id = o.conversation_id
            ),
            series_rows as (
              select
                (cr.started_at at time zone 'utc')::date as bucket_date,
                count(*)::int as n
              from convo_range cr
              group by 1
            ),
            recent_rows as (
              select
                ca.id,
                ca.visitor_id,
                ca.status,
                ca.last_activity_at,
                lm.topic_preview
              from convo_all ca
              left join lateral (
                select left(m.content, 200) as topic_preview
                from public.messages m
                where m.conversation_id = ca.id
                  and m.user_id = ca.user_id
                order by m.created_at desc
                limit 1
              ) lm on true
              order by ca.last_activity_at desc
              limit 8
            ),
            topic_counts as (
              select
                elem->>'slug' as slug,
                max(elem->>'label') as label,
                count(*)::int as n,
                bool_or(coalesce((o.payload->>'needs_follow_up_training')::boolean, false)) as needs_training
              from outcomes o
              cross join lateral jsonb_array_elements(coalesce(o.payload->'training_topics', '[]'::jsonb)) elem
              group by elem->>'slug'
            ),
            ticket_counts as (
              select
                count(*) filter (where t.status = 'open')::int as open_escalations,
                count(*) filter (where t.status = 'pending_customer')::int as awaiting_customer_reply
              from public.tickets t
              where t.user_id = cast(:user_id as uuid)
                and t.agent_id = cast(:agent_id as uuid)
            )
            select
              (select ok from agent_ok) as agent_exists,
              (select count(*)::int from convo_range) as started_n,
              (select count(*)::int from convo_all where status = 'open') as active_open_n,
              (select count(*)::int from convo_range where status = 'escalated') as escalated_n,
              (
                select count(*)::int
                from convo_range
                where status in ('resolved', 'idle_closed')
              ) as status_resolved_n,
              (select count(*)::int from outcomes) as outcome_total,
              (select open_escalations from ticket_counts) as open_escalations,
              (select awaiting_customer_reply from ticket_counts) as awaiting_customer_reply,
              coalesce(
                (
                  select jsonb_agg(
                    jsonb_build_object(
                      'bucket_date', s.bucket_date,
                      'count', s.n
                    )
                    order by s.bucket_date asc
                  )
                  from series_rows s
                ),
                '[]'::jsonb
              ) as series_json,
              coalesce(
                (
                  select jsonb_agg(
                    jsonb_build_object(
                      'conversation_id', r.id,
                      'visitor_id', r.visitor_id,
                      'topic_preview', r.topic_preview,
                      'status', r.status,
                      'last_activity_at', r.last_activity_at
                    )
                    order by r.last_activity_at desc
                  )
                  from recent_rows r
                ),
                '[]'::jsonb
              ) as recent_json,
              coalesce(
                (
                  select jsonb_agg(
                    jsonb_build_object(
                      'slug', tc.slug,
                      'label', coalesce(tc.label, tc.slug, 'Topic'),
                      'count', tc.n
                    )
                    order by tc.needs_training desc, tc.n desc, tc.slug asc
                  )
                  from (
                    select *
                    from topic_counts
                    where coalesce(slug, '') <> ''
                    order by needs_training desc, n desc, slug asc
                    limit 10
                  ) tc
                ),
                '[]'::jsonb
              ) as topics_json
            """
        ),
        params,
    )
    crow = summary.mappings().one()
    if not bool(crow["agent_exists"]):
        raise AppError(code="agent.not_found", message="Agent not found", status_code=404)
    conversations_started = int(crow["started_n"] or 0)
    active_conversations = int(crow["active_open_n"] or 0)
    escalated_n = int(crow["escalated_n"] or 0)
    status_resolved_n = int(crow["status_resolved_n"] or 0)
    aggregate_ms = round((perf_counter() - step_start) * 1000, 1)

    resolved_by_agent_pct: float | None = None
    needs_human_pct: float | None = None
    if conversations_started > 0:
        resolved_by_agent_pct = round(100.0 * status_resolved_n / conversations_started, 1)
        needs_human_pct = round(100.0 * escalated_n / conversations_started, 1)

    series = [
        DashboardSeriesPoint.model_validate(item)
        for item in (crow["series_json"] or [])
    ]
    recent = [
        DashboardRecentRow.model_validate(item)
        for item in (crow["recent_json"] or [])
    ]
    training_topics = [
        TrainingTopicSummary.model_validate(item)
        for item in (crow["topics_json"] or [])
    ]
    plan_slug = await fetch_active_plan_slug(db, user_id)
    sources_suggestions_enabled = sources_suggestions_enabled_for_plan_slug(plan_slug)
    if not sources_suggestions_enabled:
        training_topics = []

    open_escalations = int(crow["open_escalations"] or 0)
    awaiting_customer = int(crow["awaiting_customer_reply"] or 0)

    total_ms = round((perf_counter() - request_start) * 1000, 1)
    log.info(
        "dashboard.build.completed",
        user_id=user_s,
        agent_id=agent_s,
        tick_lifecycle=tick_lifecycle,
        agent_lookup_ms=agent_lookup_ms,
        lifecycle_ms=lifecycle_ms,
        lifecycle_idle_closed=lifecycle_idle_n,
        lifecycle_outcomes=lifecycle_outcome_n,
        aggregate_ms=aggregate_ms,
        total_ms=total_ms,
    )

    return AgentDashboardResponse(
        range_from=rf,
        range_to=rt,
        conversations_started=conversations_started,
        active_conversations=active_conversations,
        resolved_by_agent_pct=resolved_by_agent_pct,
        needs_human_pct=needs_human_pct,
        open_escalations=open_escalations,
        awaiting_customer_reply=awaiting_customer,
        series=series,
        recent=recent,
        training_topics=training_topics,
        sources_suggestions_enabled=sources_suggestions_enabled,
    )
