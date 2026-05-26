"""Cost roll-ups for the admin panel.

Per-conversation and per-user numbers are computed live (cheap, single user / single
conversation scope). Platform-wide queries are wrapped in a small in-memory TTL cache
because they scan all messages and run for every dashboard render.

`Settings`-driven pricing flows through `costing.build_llm_cost_usd_expr` / `compute_message_cost_usd`
so adding a new model is a config edit + restart.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.settings import Settings, get_settings
from app.domains.admin.costing import (
    build_embedding_cost_usd_expr,
    build_llm_cost_usd_expr,
    compute_message_cost_usd,
    embedding_pricing_status,
    unknown_models_warning,
)
from app.domains.admin.schemas import (
    AdminConversationCost,
    AdminCostByAgentRow,
    AdminCostByModelRow,
    AdminCostEventRow,
    AdminCostingLeaderboard,
    AdminCostingPeriod,
    AdminCostKindRollup,
    AdminCostPerTurnRollup,
    AdminMessageCostRow,
    AdminPlatformCosting,
    AdminUserCosting,
    AdminUserCostingRow,
)

# --- Tiny in-process TTL cache ---------------------------------------------------
# Per-process. Multi-worker setups will see brief inconsistencies between workers,
# which is acceptable — admin panel is internal-only and refreshes often.
PLATFORM_CACHE_TTL_SECONDS = 60
_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.monotonic() > expires_at:
        _cache.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: Any, ttl: int = PLATFORM_CACHE_TTL_SECONDS) -> None:
    _cache[key] = (time.monotonic() + ttl, value)


def _clear_cache() -> None:
    """Test helper — flush the entire process cache."""
    _cache.clear()


# --- Period helpers --------------------------------------------------------------


def _current_month_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Returns [start_of_current_month_utc, start_of_next_month_utc) as aware datetimes."""
    now = (now or datetime.now(tz=UTC)).astimezone(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _prior_month_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    cur_start, _ = _current_month_window(now)
    if cur_start.month == 1:
        prior_start = cur_start.replace(year=cur_start.year - 1, month=12)
    else:
        prior_start = cur_start.replace(month=cur_start.month - 1)
    return prior_start, cur_start


def _safe_margin_pct(margin: float, revenue: float) -> float | None:
    if revenue <= 0:
        return None
    return (margin / revenue) * 100.0


# --- Conversation cost -----------------------------------------------------------


async def get_conversation_cost(
    db: AsyncSession,
    conversation_id: UUID,
    *,
    settings: Settings | None = None,
) -> AdminConversationCost:
    settings = settings or get_settings()

    # Existence check (mirrors get_admin_conversation_detail's 404 behavior).
    head = (
        await db.execute(
            text("select id from public.conversations where id = :conversation_id"),
            {"conversation_id": str(conversation_id)},
        )
    ).first()
    if head is None:
        raise AppError(
            code="admin.conversation_not_found",
            message="Conversation not found",
            status_code=404,
        )

    rows = (
        await db.execute(
            text(
                """
                select id, role, model, input_tokens, output_tokens, created_at
                from public.messages
                where conversation_id = :conversation_id
                order by created_at asc
                """
            ),
            {"conversation_id": str(conversation_id)},
        )
    ).mappings().all()

    messages: list[AdminMessageCostRow] = []
    by_model_acc: dict[str, dict[str, float]] = {}
    total_in = 0
    total_out = 0
    total_cost = 0.0
    has_priced = False
    has_unknown = False

    for row in rows:
        cost = compute_message_cost_usd(
            settings, row["model"], int(row["input_tokens"] or 0), int(row["output_tokens"] or 0)
        )
        in_t = int(row["input_tokens"] or 0)
        out_t = int(row["output_tokens"] or 0)
        total_in += in_t
        total_out += out_t
        if cost is not None:
            has_priced = True
            total_cost += cost
            model = row["model"]
            slot = by_model_acc.setdefault(
                model, {"in": 0.0, "out": 0.0, "emb": 0.0, "cost": 0.0}
            )
            slot["in"] += in_t
            slot["out"] += out_t
            slot["cost"] += cost
        elif row["model"]:
            # Has a model but no priced expression — surface this as "unknown pricing".
            has_unknown = True
        messages.append(
            AdminMessageCostRow(
                id=row["id"],
                role=row["role"],
                model=row["model"],
                input_tokens=in_t,
                output_tokens=out_t,
                cost_usd=cost,
                created_at=row["created_at"],
            )
        )

    by_model: list[AdminCostByModelRow] = []
    if has_priced and total_cost > 0:
        for model, slot in sorted(by_model_acc.items(), key=lambda kv: -kv[1]["cost"]):
            by_model.append(
                AdminCostByModelRow(
                    model=model,
                    input_tokens=int(slot["in"]),
                    output_tokens=int(slot["out"]),
                    embedding_tokens=int(slot.get("emb", 0.0)),
                    cost_usd=slot["cost"],
                    pct_of_total=(slot["cost"] / total_cost) * 100.0,
                )
            )

    conv_meta = (
        await db.execute(
            text(
                "select customer_message_count from public.conversations where id = :conversation_id"
            ),
            {"conversation_id": str(conversation_id)},
        )
    ).mappings().first()
    customer_message_count = int(conv_meta["customer_message_count"] or 0) if conv_meta else 0

    ev_rows = (
        await db.execute(
            text(
                """
                select id, kind, provider_model, turn_user_message_id,
                       input_tokens, output_tokens, embedding_tokens, cost_usd, metadata, created_at
                from public.conversation_cost_events
                where conversation_id = :conversation_id
                order by created_at asc
                """
            ),
            {"conversation_id": str(conversation_id)},
        )
    ).mappings().all()

    cost_events: list[AdminCostEventRow] = []
    by_kind_acc: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "count": 0.0})
    per_turn_acc: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "count": 0.0})
    ev_by_model: dict[str, dict[str, float]] = defaultdict(lambda: {"in": 0.0, "out": 0.0, "emb": 0.0, "cost": 0.0})
    events_total = 0.0
    has_unknown_event_pricing = False

    for er in ev_rows:
        meta = er["metadata"] if isinstance(er["metadata"], dict) else {}
        in_t = int(er["input_tokens"] or 0)
        out_t = int(er["output_tokens"] or 0)
        emb_t = int(er["embedding_tokens"] or 0)
        c_raw = er["cost_usd"]
        has_token_activity = in_t > 0 or out_t > 0 or emb_t > 0
        if c_raw is None and has_token_activity:
            has_unknown_event_pricing = True
        c_val = float(c_raw) if c_raw is not None else 0.0
        events_total += c_val

        kind_s = str(er["kind"] or "")
        by_kind_acc[kind_s]["cost"] += c_val
        by_kind_acc[kind_s]["count"] += 1.0

        tid = er["turn_user_message_id"]
        if tid is not None:
            ts = str(tid)
            per_turn_acc[ts]["cost"] += c_val
            per_turn_acc[ts]["count"] += 1.0

        mkey = (str(er["provider_model"]).strip() if er["provider_model"] else "") or f"[{kind_s}]"
        ev_by_model[mkey]["cost"] += c_val
        ev_by_model[mkey]["in"] += in_t
        ev_by_model[mkey]["out"] += out_t
        ev_by_model[mkey]["emb"] += emb_t

        cost_events.append(
            AdminCostEventRow(
                id=er["id"],
                kind=kind_s,
                provider_model=er["provider_model"],
                turn_user_message_id=er["turn_user_message_id"],
                input_tokens=in_t,
                output_tokens=out_t,
                embedding_tokens=emb_t,
                cost_usd=float(c_raw) if c_raw is not None else None,
                metadata=meta,
                created_at=er["created_at"],
            )
        )

    by_kind_list = [
        AdminCostKindRollup(kind=k, cost_usd=float(v["cost"]), count=int(v["count"]))
        for k, v in sorted(by_kind_acc.items(), key=lambda kv: -kv[1]["cost"])
    ]
    per_turn_list = [
        AdminCostPerTurnRollup(
            turn_user_message_id=UUID(tid),
            cost_usd=float(v["cost"]),
            event_count=int(v["count"]),
        )
        for tid, v in sorted(per_turn_acc.items(), key=lambda kv: kv[0])
    ]

    events_total_cost_usd: float | None = None
    avg_cost_per_customer_message_usd: float | None = None
    display_by_model = by_model
    display_total_cost = total_cost if has_priced else None

    if ev_rows:
        events_total_cost_usd = None if has_unknown_event_pricing else events_total
        display_total_cost = events_total_cost_usd
        if (
            customer_message_count > 0
            and events_total_cost_usd is not None
        ):
            avg_cost_per_customer_message_usd = events_total_cost_usd / float(customer_message_count)
        ev_total_for_pct = events_total if events_total > 0 else 1.0
        display_by_model = [
            AdminCostByModelRow(
                model=m,
                input_tokens=int(v["in"]),
                output_tokens=int(v["out"]),
                embedding_tokens=int(v["emb"]),
                cost_usd=float(v["cost"]),
                pct_of_total=(float(v["cost"]) / ev_total_for_pct) * 100.0,
            )
            for m, v in sorted(ev_by_model.items(), key=lambda kv: -kv[1]["cost"])
        ]

    return AdminConversationCost(
        conversation_id=conversation_id,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_cost_usd=display_total_cost,
        by_model=display_by_model,
        messages=messages,
        has_unknown_models=has_unknown or has_unknown_event_pricing,
        cost_events=cost_events,
        events_total_cost_usd=events_total_cost_usd,
        has_unknown_event_pricing=has_unknown_event_pricing,
        by_kind=by_kind_list,
        per_turn=per_turn_list,
        customer_message_count=customer_message_count,
        avg_cost_per_customer_message_usd=avg_cost_per_customer_message_usd,
    )


# --- User costing (MTD) ----------------------------------------------------------


async def get_user_costing(
    db: AsyncSession,
    user_id: UUID,
    *,
    settings: Settings | None = None,
) -> AdminUserCosting:
    settings = settings or get_settings()
    period_start, period_end = _current_month_window()

    user_row = (
        await db.execute(
            text("select email from auth.users where id = :user_id"),
            {"user_id": str(user_id)},
        )
    ).mappings().first()
    if user_row is None:
        raise AppError(code="admin.user_not_found", message="User not found", status_code=404)

    # Revenue: monthly_price_cents on the latest active subscription. Plans without an
    # active sub contribute $0 — matches "no plan -> no revenue" intuition.
    revenue_cents = (
        await db.execute(
            text(
                """
                select coalesce(p.monthly_price_cents, 0)::int as cents
                from public.subscriptions s
                join public.plans p on p.id = s.plan_id
                where s.user_id = :user_id
                  and s.status in ('active', 'trialing')
                order by s.current_period_end desc
                limit 1
                """
            ),
            {"user_id": str(user_id)},
        )
    ).scalar()
    revenue_usd = float(revenue_cents or 0) / 100.0

    llm_expr = build_llm_cost_usd_expr(
        settings.llm_input_price_per_million_usd,
        settings.llm_output_price_per_million_usd,
        alias="m",
    )
    embedding_expr = build_embedding_cost_usd_expr(
        settings.embedding_price_per_million_usd,
        settings.openai_embedding_model,
        alias="kc",
    )

    llm_cost = float(
        (
            await db.execute(
                text(
                    f"""
                    select coalesce(sum({llm_expr}), 0.0)::float as v
                    from public.messages m
                    where m.user_id = :user_id
                      and m.created_at >= :period_start
                      and m.created_at < :period_end
                    """
                ),
                {
                    "user_id": str(user_id),
                    "period_start": period_start,
                    "period_end": period_end,
                },
            )
        ).scalar()
        or 0.0
    )

    embedding_cost = float(
        (
            await db.execute(
                text(
                    f"""
                    select coalesce(sum({embedding_expr}), 0.0)::float as v
                    from public.knowledge_chunks kc
                    where kc.user_id = :user_id
                      and kc.created_at >= :period_start
                      and kc.created_at < :period_end
                    """
                ),
                {
                    "user_id": str(user_id),
                    "period_start": period_start,
                    "period_end": period_end,
                },
            )
        ).scalar()
        or 0.0
    )

    by_agent_rows = (
        await db.execute(
            text(
                f"""
                with agent_llm as (
                  select
                    a.id as agent_id,
                    a.name as agent_name,
                    coalesce(sum({llm_expr}), 0.0)::float as llm_cost,
                    count(distinct m.conversation_id)::int as conversations,
                    count(*)::int as messages
                  from public.agents a
                  left join public.messages m
                    on m.agent_id = a.id
                   and m.created_at >= :period_start
                   and m.created_at < :period_end
                  where a.user_id = :user_id
                  group by a.id, a.name
                ),
                agent_embed as (
                  select
                    ks.agent_id,
                    coalesce(sum({embedding_expr}), 0.0)::float as embedding_cost
                  from public.knowledge_sources ks
                  join public.knowledge_chunks kc on kc.knowledge_source_id = ks.id
                  where ks.user_id = :user_id
                    and kc.created_at >= :period_start
                    and kc.created_at < :period_end
                  group by ks.agent_id
                )
                select
                  l.agent_id, l.agent_name,
                  l.llm_cost, coalesce(e.embedding_cost, 0.0)::float as embedding_cost,
                  l.conversations, l.messages
                from agent_llm l
                left join agent_embed e on e.agent_id = l.agent_id
                order by (l.llm_cost + coalesce(e.embedding_cost, 0.0)) desc, l.agent_name asc
                """
            ),
            {
                "user_id": str(user_id),
                "period_start": period_start,
                "period_end": period_end,
            },
        )
    ).mappings().all()

    by_agent = [
        AdminCostByAgentRow(
            agent_id=r["agent_id"],
            agent_name=r["agent_name"],
            llm_cost_usd=float(r["llm_cost"]),
            embedding_cost_usd=float(r["embedding_cost"]),
            total_cost_usd=float(r["llm_cost"]) + float(r["embedding_cost"]),
            conversations=int(r["conversations"]),
            messages=int(r["messages"]),
        )
        for r in by_agent_rows
    ]

    total_cost = llm_cost + embedding_cost
    margin = revenue_usd - total_cost
    return AdminUserCosting(
        user_id=user_id,
        email=user_row["email"],
        period_label="MTD",
        revenue_usd=revenue_usd,
        llm_cost_usd=llm_cost,
        embedding_cost_usd=embedding_cost,
        total_cost_usd=total_cost,
        margin_usd=margin,
        margin_pct=_safe_margin_pct(margin, revenue_usd),
        by_agent=by_agent,
    )


# --- Platform overview -----------------------------------------------------------


async def _period_costs(
    db: AsyncSession,
    *,
    period_start: datetime,
    period_end: datetime,
    llm_expr: str,
    embedding_expr: str,
) -> tuple[float, float, float]:
    """Returns (llm_cost, embedding_cost, revenue) for a window. Revenue is the sum of
    monthly_price_cents over all currently-active subs (point-in-time, not pro-rated)."""
    llm = float(
        (
            await db.execute(
                text(
                    f"""
                    select coalesce(sum({llm_expr}), 0.0)::float as v
                    from public.messages m
                    where m.created_at >= :period_start and m.created_at < :period_end
                    """
                ),
                {"period_start": period_start, "period_end": period_end},
            )
        ).scalar()
        or 0.0
    )
    embedding = float(
        (
            await db.execute(
                text(
                    f"""
                    select coalesce(sum({embedding_expr}), 0.0)::float as v
                    from public.knowledge_chunks kc
                    where kc.created_at >= :period_start and kc.created_at < :period_end
                    """
                ),
                {"period_start": period_start, "period_end": period_end},
            )
        ).scalar()
        or 0.0
    )
    revenue_cents = int(
        (
            await db.execute(
                text(
                    """
                    select coalesce(sum(p.monthly_price_cents), 0)::int as v
                    from public.subscriptions s
                    join public.plans p on p.id = s.plan_id
                    where s.status in ('active', 'trialing')
                      and s.current_period_start < :period_end
                      and s.current_period_end >= :period_start
                    """
                ),
                {"period_start": period_start, "period_end": period_end},
            )
        ).scalar()
        or 0
    )
    return llm, embedding, float(revenue_cents) / 100.0


async def get_platform_costing_overview(
    db: AsyncSession,
    *,
    settings: Settings | None = None,
    use_cache: bool = True,
) -> AdminPlatformCosting:
    settings = settings or get_settings()
    cache_key = "platform_overview"
    if use_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    cur_start, cur_end = _current_month_window()
    prior_start, prior_end = _prior_month_window()

    llm_expr = build_llm_cost_usd_expr(
        settings.llm_input_price_per_million_usd,
        settings.llm_output_price_per_million_usd,
        alias="m",
    )
    embedding_expr = build_embedding_cost_usd_expr(
        settings.embedding_price_per_million_usd,
        settings.openai_embedding_model,
        alias="kc",
    )

    cur_llm, cur_embed, cur_rev = await _period_costs(
        db,
        period_start=cur_start,
        period_end=cur_end,
        llm_expr=llm_expr,
        embedding_expr=embedding_expr,
    )
    prior_llm, prior_embed, prior_rev = await _period_costs(
        db,
        period_start=prior_start,
        period_end=prior_end,
        llm_expr=llm_expr,
        embedding_expr=embedding_expr,
    )

    cur_total = cur_llm + cur_embed
    cur_margin = cur_rev - cur_total
    prior_total = prior_llm + prior_embed
    prior_margin = prior_rev - prior_total

    current = AdminCostingPeriod(
        label="MTD",
        period_start=cur_start,
        period_end=cur_end,
        revenue_usd=cur_rev,
        llm_cost_usd=cur_llm,
        embedding_cost_usd=cur_embed,
        total_cost_usd=cur_total,
        gross_margin_usd=cur_margin,
        gross_margin_pct=_safe_margin_pct(cur_margin, cur_rev),
    )
    prior = AdminCostingPeriod(
        label="Prior month",
        period_start=prior_start,
        period_end=prior_end,
        revenue_usd=prior_rev,
        llm_cost_usd=prior_llm,
        embedding_cost_usd=prior_embed,
        total_cost_usd=prior_total,
        gross_margin_usd=prior_margin,
        gross_margin_pct=_safe_margin_pct(prior_margin, prior_rev),
    )

    by_model_rows = (
        await db.execute(
            text(
                f"""
                select
                  m.model,
                  sum(m.input_tokens)::bigint as input_tokens,
                  sum(m.output_tokens)::bigint as output_tokens,
                  coalesce(sum({llm_expr}), 0.0)::float as cost
                from public.messages m
                where m.created_at >= :period_start and m.created_at < :period_end
                  and m.model is not null
                group by m.model
                order by cost desc nulls last, m.model asc
                """
            ),
            {"period_start": cur_start, "period_end": cur_end},
        )
    ).mappings().all()

    by_model: list[AdminCostByModelRow] = []
    for r in by_model_rows:
        cost = float(r["cost"] or 0.0)
        pct = (cost / cur_llm) * 100.0 if cur_llm > 0 else 0.0
        by_model.append(
            AdminCostByModelRow(
                model=r["model"],
                input_tokens=int(r["input_tokens"] or 0),
                output_tokens=int(r["output_tokens"] or 0),
                cost_usd=cost,
                pct_of_total=pct,
            )
        )

    observed_models = (
        await db.execute(
            text(
                """
                select distinct m.model
                from public.messages m
                where m.model is not null
                  and m.created_at >= :period_start and m.created_at < :period_end
                """
            ),
            {"period_start": cur_start, "period_end": cur_end},
        )
    ).scalars().all()
    unknown = unknown_models_warning(observed_models, settings)

    embed_priced, embed_model = embedding_pricing_status(settings)

    overview = AdminPlatformCosting(
        period_label="MTD vs prior month",
        current=current,
        prior=prior,
        by_model=by_model,
        unknown_models=unknown,
        embedding_model=embed_model,
        embedding_model_priced=embed_priced,
        cached_at=datetime.now(tz=UTC),
        cache_ttl_seconds=PLATFORM_CACHE_TTL_SECONDS,
    )
    if use_cache:
        _cache_put(cache_key, overview)
    return overview


# --- Leaderboards ----------------------------------------------------------------


async def _user_costing_rows(
    db: AsyncSession,
    *,
    settings: Settings,
    period_start: datetime,
    period_end: datetime,
) -> list[AdminUserCostingRow]:
    """Per-user costing for the current period. One row per user with at least one of:
    a current active sub, MTD LLM cost, or MTD embedding cost."""
    llm_expr = build_llm_cost_usd_expr(
        settings.llm_input_price_per_million_usd,
        settings.llm_output_price_per_million_usd,
        alias="m",
    )
    embedding_expr = build_embedding_cost_usd_expr(
        settings.embedding_price_per_million_usd,
        settings.openai_embedding_model,
        alias="kc",
    )

    sql = f"""
        with active_subs as (
          select distinct on (s.user_id)
            s.user_id, p.slug as plan_slug, p.name as plan_name,
            p.monthly_price_cents
          from public.subscriptions s
          join public.plans p on p.id = s.plan_id
          where s.status in ('active', 'trialing')
          order by s.user_id, s.current_period_end desc
        ),
        llm_costs as (
          select m.user_id, coalesce(sum({llm_expr}), 0.0)::float as cost
          from public.messages m
          where m.created_at >= :period_start and m.created_at < :period_end
          group by m.user_id
        ),
        embed_costs as (
          select kc.user_id, coalesce(sum({embedding_expr}), 0.0)::float as cost
          from public.knowledge_chunks kc
          where kc.created_at >= :period_start and kc.created_at < :period_end
          group by kc.user_id
        )
        select
          u.id as user_id, coalesce(u.email, '') as email,
          s.plan_slug, s.plan_name,
          coalesce(s.monthly_price_cents, 0)::int as revenue_cents,
          coalesce(l.cost, 0.0)::float as llm_cost,
          coalesce(e.cost, 0.0)::float as embed_cost
        from auth.users u
        left join active_subs s on s.user_id = u.id
        left join llm_costs l on l.user_id = u.id
        left join embed_costs e on e.user_id = u.id
        where s.user_id is not null or l.user_id is not null or e.user_id is not null
    """
    rows = (
        await db.execute(
            text(sql),
            {"period_start": period_start, "period_end": period_end},
        )
    ).mappings().all()

    out: list[AdminUserCostingRow] = []
    for r in rows:
        revenue = float(r["revenue_cents"] or 0) / 100.0
        llm = float(r["llm_cost"] or 0.0)
        embed = float(r["embed_cost"] or 0.0)
        total = llm + embed
        margin = revenue - total
        out.append(
            AdminUserCostingRow(
                user_id=r["user_id"],
                email=r["email"],
                plan_slug=r["plan_slug"],
                plan_name=r["plan_name"],
                revenue_usd=revenue,
                llm_cost_usd=llm,
                embedding_cost_usd=embed,
                total_cost_usd=total,
                margin_usd=margin,
                margin_pct=_safe_margin_pct(margin, revenue),
            )
        )
    return out


async def get_worst_margin_leaderboard(
    db: AsyncSession,
    *,
    limit: int = 20,
    settings: Settings | None = None,
    use_cache: bool = True,
) -> AdminCostingLeaderboard:
    settings = settings or get_settings()
    capped_limit = max(1, min(limit, 100))
    cache_key = f"worst_margin:{capped_limit}"
    if use_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    cur_start, cur_end = _current_month_window()
    rows = await _user_costing_rows(
        db, settings=settings, period_start=cur_start, period_end=cur_end
    )
    # margin asc (most negative first); break ties with cost desc so big spenders surface first
    rows.sort(key=lambda r: (r.margin_usd, -r.total_cost_usd))
    items = rows[:capped_limit]

    leaderboard = AdminCostingLeaderboard(
        metric="worst_margin",
        items=items,
        cached_at=datetime.now(tz=UTC),
        cache_ttl_seconds=PLATFORM_CACHE_TTL_SECONDS,
    )
    if use_cache:
        _cache_put(cache_key, leaderboard)
    return leaderboard


async def get_top_spenders_leaderboard(
    db: AsyncSession,
    *,
    limit: int = 20,
    settings: Settings | None = None,
    use_cache: bool = True,
) -> AdminCostingLeaderboard:
    settings = settings or get_settings()
    capped_limit = max(1, min(limit, 100))
    cache_key = f"top_spenders:{capped_limit}"
    if use_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    cur_start, cur_end = _current_month_window()
    rows = await _user_costing_rows(
        db, settings=settings, period_start=cur_start, period_end=cur_end
    )
    rows.sort(key=lambda r: -r.total_cost_usd)
    items = rows[:capped_limit]

    leaderboard = AdminCostingLeaderboard(
        metric="top_spend",
        items=items,
        cached_at=datetime.now(tz=UTC),
        cache_ttl_seconds=PLATFORM_CACHE_TTL_SECONDS,
    )
    if use_cache:
        _cache_put(cache_key, leaderboard)
    return leaderboard
