from __future__ import annotations

from datetime import datetime
from time import perf_counter
from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.domains.analytics.schemas import (
    AgentAnalyticsResponse,
    AnalyticsNamedCount,
    AnalyticsQualityMetric,
    AnalyticsSentimentSlice,
    AnalyticsSeriesPoint,
)
from app.domains.conversation_outcomes.service import tick_idle_and_outcomes
from app.domains.dashboard.service import resolve_dashboard_range

log = structlog.get_logger("analytics")


async def build_agent_analytics(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    analytics_tier: Literal["basic", "full"],
    range_key: str | None,
    range_from: datetime | None,
    range_to: datetime | None,
    tick_lifecycle: bool = True,
) -> AgentAnalyticsResponse:
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
    analytics_row = await db.execute(
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
            convo_range as (
              select
                c.id,
                c.status,
                c.started_at,
                c.metadata
              from public.conversations c
              where c.user_id = cast(:user_id as uuid)
                and c.agent_id = cast(:agent_id as uuid)
                and c.started_at >= :rf
                and c.started_at < :rt
                and (select ok from agent_ok)
            ),
            outcomes as (
              select
                o.payload,
                cr.status as conversation_status
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
            country_rows as (
              select
                coalesce(nullif(upper(trim(cr.metadata->>'country_code')), ''), 'UNKNOWN') as code,
                count(*)::int as n
              from convo_range cr
              group by 1
              order by n desc, code asc
              limit 12
            ),
            intent_rows as (
              select
                nullif(trim(o.payload->>'primary_intent_slug'), '') as slug,
                max(nullif(trim(o.payload->>'primary_intent'), '')) as label,
                count(*)::int as n
              from outcomes o
              where length(coalesce(nullif(trim(o.payload->>'primary_intent_slug'), ''), '')) > 0
              group by 1
              order by n desc, slug asc
              limit 10
            ),
            signal_rows as (
              select
                m.conversation_id,
                m.metadata,
                m.created_at
              from public.messages m
              join convo_range cr on cr.id = m.conversation_id
              where m.role = 'assistant'
                and m.metadata ? 'turn_signals'
            ),
            last_turn as (
              select distinct on (sr.conversation_id)
                case
                  when (sr.metadata->'turn_signals'->>'customer_sentiment') in ('positive') then 'positive'
                  when (sr.metadata->'turn_signals'->>'customer_sentiment') in ('negative', 'frustrated') then 'negative'
                  else 'neutral'
                end as bucket
              from signal_rows sr
              where sr.metadata->'turn_signals'->>'customer_sentiment' is not null
              order by sr.conversation_id, sr.created_at desc
            ),
            sentiment_counts as (
              select
                count(*) filter (where lt.bucket = 'positive')::int as pos_n,
                count(*) filter (where lt.bucket = 'neutral')::int as neu_n,
                count(*) filter (where lt.bucket = 'negative')::int as neg_n
              from last_turn lt
            ),
            gap_counts as (
              select
                count(*) filter (
                  where (sr.metadata->'turn_signals'->>'knowledge_gap')::boolean is true
                )::int as gap_n,
                count(*)::int as gap_total
              from signal_rows sr
            )
            select
              (select ok from agent_ok) as agent_exists,
              (select count(*)::int from convo_range) as started_n,
              (select count(*)::int from convo_range where status = 'escalated') as escalated_n,
              (
                select count(*)::int
                from convo_range
                where status in ('resolved', 'idle_closed')
              ) as status_resolved_n,
              (select count(*)::int from outcomes) as outcome_total,
              (
                select count(*)::int
                from outcomes o
                where (o.payload->>'resolved_by_agent')::boolean is true
                  and o.conversation_status <> 'escalated'
              ) as resolved_not_escalated_n,
              (
                select avg((o.payload->>'resolution_confidence')::double precision)
                from outcomes o
              ) as avg_conf,
              (
                select avg(m.latency_ms)::double precision
                from public.messages m
                join convo_range cr on cr.id = m.conversation_id
                where m.role = 'assistant'
                  and m.latency_ms is not null
              ) as avg_ms,
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
                      'key', c.code,
                      'label', case when c.code = 'UNKNOWN' then 'Unknown' else c.code end,
                      'count', c.n
                    )
                    order by c.n desc, c.code asc
                  )
                  from country_rows c
                ),
                '[]'::jsonb
              ) as countries_json,
              coalesce(
                (
                  select jsonb_agg(
                    jsonb_build_object(
                      'key', i.slug,
                      'label', coalesce(i.label, i.slug, 'Intent'),
                      'count', i.n
                    )
                    order by i.n desc, i.slug asc
                  )
                  from intent_rows i
                ),
                '[]'::jsonb
              ) as intents_json,
              (select pos_n from sentiment_counts) as pos_n,
              (select neu_n from sentiment_counts) as neu_n,
              (select neg_n from sentiment_counts) as neg_n,
              (select gap_n from gap_counts) as gap_n,
              (select gap_total from gap_counts) as gap_total
            """
        ),
        params,
    )
    crow = analytics_row.mappings().one()
    if not bool(crow["agent_exists"]):
        raise AppError(code="agent.not_found", message="Agent not found", status_code=404)
    conversations_started = int(crow["started_n"] or 0)
    escalated_n = int(crow["escalated_n"] or 0)
    status_resolved_n = int(crow["status_resolved_n"] or 0)
    outcome_total = int(crow["outcome_total"] or 0)
    resolved_not_escalated_n = int(crow["resolved_not_escalated_n"] or 0)
    avg_conf = crow["avg_conf"]
    avg_ms_raw = crow["avg_ms"]
    main_aggregate_ms = round((perf_counter() - step_start) * 1000, 1)

    escalations_pct: float | None = None
    if conversations_started > 0:
        escalations_pct = round(100.0 * escalated_n / conversations_started, 1)

    resolved_by_agent_pct: float | None = None
    if conversations_started > 0:
        resolved_by_agent_pct = round(100.0 * status_resolved_n / conversations_started, 1)

    avg_response_time_ms: float | None = None
    if avg_ms_raw is not None:
        avg_response_time_ms = round(float(avg_ms_raw), 1)

    series = [
        AnalyticsSeriesPoint.model_validate(item)
        for item in (crow["series_json"] or [])
    ]
    countries = [
        AnalyticsNamedCount.model_validate(item)
        for item in (crow["countries_json"] or [])
    ]
    top_intents = [
        AnalyticsNamedCount.model_validate(item)
        for item in (crow["intents_json"] or [])
        if (item.get("key") if isinstance(item, dict) else True)
    ]
    intents_ms = 0.0

    sentiment_quality_ms = 0.0
    pos = int(crow["pos_n"] or 0)
    neu = int(crow["neu_n"] or 0)
    neg = int(crow["neg_n"] or 0)
    sent_total = pos + neu + neg
    sentiment: list[AnalyticsSentimentSlice] = []
    for bucket, count in (("positive", pos), ("neutral", neu), ("negative", neg)):
        pct = round(100.0 * count / sent_total, 1) if sent_total > 0 else None
        sentiment.append(AnalyticsSentimentSlice(bucket=bucket, count=count, pct=pct))
    gap_n = int(crow["gap_n"] or 0)
    gap_total = int(crow["gap_total"] or 0)
    gap_pct: float | None = None
    if gap_total > 0:
        gap_pct = round(100.0 * gap_n / gap_total, 1)

    fcr_pct: float | None = None
    if outcome_total > 0:
        fcr_pct = round(100.0 * resolved_not_escalated_n / outcome_total, 1)

    avg_conf_pct: str | None = None
    if avg_conf is not None:
        avg_conf_pct = f"{round(float(avg_conf) * 100.0, 1)}%"

    quality: list[AnalyticsQualityMetric] = [
        AnalyticsQualityMetric(
            key="resolution_confidence",
            label="Avg resolution confidence",
            value=avg_conf_pct or "—",
            hint="From end-of-conversation analysis (closed chats in range).",
        ),
        AnalyticsQualityMetric(
            key="resolved_no_escalation",
            label="Resolved without escalation",
            value=f"{fcr_pct}%" if fcr_pct is not None else "—",
            hint="AI marked resolved and conversation was not escalated.",
        ),
        AnalyticsQualityMetric(
            key="knowledge_gap_turns",
            label="Turns flagged knowledge gap",
            value=f"{gap_pct}%" if gap_pct is not None else "—",
            hint="Share of assistant replies with per-turn gap signal.",
        ),
    ]

    total_ms = round((perf_counter() - request_start) * 1000, 1)
    log.info(
        "analytics.build.completed",
        user_id=user_s,
        agent_id=agent_s,
        tick_lifecycle=tick_lifecycle,
        agent_lookup_ms=agent_lookup_ms,
        lifecycle_ms=lifecycle_ms,
        lifecycle_idle_closed=lifecycle_idle_n,
        lifecycle_outcomes=lifecycle_outcome_n,
        main_aggregate_ms=main_aggregate_ms,
        intents_ms=intents_ms,
        sentiment_quality_ms=sentiment_quality_ms,
        total_ms=total_ms,
    )

    if analytics_tier == "basic":
        return AgentAnalyticsResponse(
            analytics_tier="basic",
            range_from=rf,
            range_to=rt,
            conversations_started=conversations_started,
            resolved_by_agent_pct=resolved_by_agent_pct,
            escalations_pct=escalations_pct,
            avg_response_time_ms=avg_response_time_ms,
            series=series,
            top_intents=[],
            sentiment=[],
            countries=[],
            quality=[],
        )

    return AgentAnalyticsResponse(
        analytics_tier="full",
        range_from=rf,
        range_to=rt,
        conversations_started=conversations_started,
        resolved_by_agent_pct=resolved_by_agent_pct,
        escalations_pct=escalations_pct,
        avg_response_time_ms=avg_response_time_ms,
        series=series,
        top_intents=top_intents,
        sentiment=sentiment,
        countries=countries,
        quality=quality,
    )
