"""Agent action catalog and persistence."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.settings import get_settings
from app.domains.actions.catalog_definitions import (
    StaticActionDefinition,
    all_static_definitions,
    get_static_definition,
)
from app.domains.actions.schemas import (
    ActionCatalogEntry,
    ActionCatalogResponse,
    AgentActionPatchRequest,
)
from app.domains.agents.service import _fetch_agent_by_id
from app.domains.bootstrap.service import _ensure_default_subscription
from app.domains.integrations.shopify.schemas import ShopifyConnectionStatus
from app.domains.integrations.shopify.service import get_connection_status
from app.domains.plans.plan_limits import plan_limits_dto_from_row

log = structlog.get_logger(__name__)

_actions_runtime_cache: dict[tuple[str, str], tuple[tuple[Any, ...], float]] = {}
_actions_runtime_lock = asyncio.Lock()


def invalidate_shopify_actions_runtime_cache(*, user_id: UUID, agent_id: UUID) -> None:
    _actions_runtime_cache.pop((str(user_id), str(agent_id)), None)


async def _ensure_action_rows(db: AsyncSession, agent_id: UUID) -> None:
    for d in all_static_definitions():
        await db.execute(
            text(
                """
                insert into public.agent_actions (agent_id, action_key, enabled, config, safety_policy)
                values (cast(:agent_id as uuid), :action_key, false, '{}'::jsonb, '{}'::jsonb)
                on conflict (agent_id, action_key) do nothing
                """
            ),
            {"agent_id": str(agent_id), "action_key": d.action_key},
        )
    await db.commit()


async def _load_agent_action_map(db: AsyncSession, agent_id: UUID) -> dict[str, dict[str, Any]]:
    result = await db.execute(
        text(
            """
            select action_key, enabled, config, safety_policy
            from public.agent_actions
            where agent_id = cast(:agent_id as uuid)
            """
        ),
        {"agent_id": str(agent_id)},
    )
    rows = result.mappings().all()
    return {
        str(r["action_key"]): {
            "enabled": bool(r["enabled"]),
            "config": dict(r["config"] or {}),
            "safety_policy": dict(r["safety_policy"] or {}),
        }
        for r in rows
    }


HUMAN_ACTION_KEY = "human.escalate"


def _scopes_satisfied(required: frozenset[str], granted: frozenset[str]) -> bool:
    return required <= granted


def _effective_status(
    *,
    shopify_plan_ok: bool,
    human_escalation_plan_ok: bool,
    max_enabled_actions_per_agent: int,
    granted: frozenset[str],
    definition: StaticActionDefinition,
) -> str:
    if not definition.requires_shopify_connection:
        if not definition.code_ready:
            return "coming_soon"
        if definition.action_key == HUMAN_ACTION_KEY and (
            not human_escalation_plan_ok or max_enabled_actions_per_agent <= 0
        ):
            return "blocked_by_plan"
        return "live"
    if not shopify_plan_ok:
        return "blocked_by_plan"
    if not definition.code_ready:
        return "coming_soon"
    # Ship-ready tools stay "live" in the catalog; `scopes_satisfied` on the entry
    # reflects OAuth / connection (including disconnected store — not "coming soon").
    return "live"


async def _count_enabled_shopify_agent_actions(db: AsyncSession, agent_id: UUID) -> int:
    result = await db.execute(
        text(
            """
            select count(*)::int as n
            from public.agent_actions
            where agent_id = cast(:agent_id as uuid)
              and enabled = true
              and action_key like 'shopify.%'
            """
        ),
        {"agent_id": str(agent_id)},
    )
    return int(result.mappings().one()["n"])


async def fetch_action_catalog_response(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    shopify_connection: ShopifyConnectionStatus | None = None,
) -> ActionCatalogResponse:
    entries = await build_catalog(
        db, user_id=user_id, agent_id=agent_id, shopify_connection=shopify_connection
    )
    _, plan = await _ensure_default_subscription(db, user_id)
    lim = plan_limits_dto_from_row(
        included_conversations=plan.included_conversations,
        max_agents=plan.max_agents,
        features=plan.features,
    )
    enabled_n = await _count_enabled_shopify_agent_actions(db, agent_id)
    return ActionCatalogResponse(
        entries=entries,
        max_enabled_shopify_actions=lim.max_enabled_actions_per_agent,
        enabled_shopify_actions=enabled_n,
    )


async def build_catalog(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    shopify_connection: ShopifyConnectionStatus | None = None,
) -> list[ActionCatalogEntry]:
    await _fetch_agent_by_id(db, user_id, agent_id)
    _, plan = await _ensure_default_subscription(db, user_id)
    features = plan.features or {}
    shopify_plan_ok = bool(features.get("shopify_enabled", False))
    human_escalation_plan_ok = bool(features.get("human_escalation_enabled", True))
    limits = plan_limits_dto_from_row(
        included_conversations=plan.included_conversations,
        max_agents=plan.max_agents,
        features=features,
    )
    max_actions = limits.max_enabled_actions_per_agent

    conn = shopify_connection or await get_connection_status(db, user_id=user_id, agent_id=agent_id)
    granted_set = frozenset((s or "").lower() for s in (conn.scopes or []))
    rows = await _load_agent_action_map(db, agent_id)

    out: list[ActionCatalogEntry] = []
    for d in all_static_definitions():
        row = rows.get(d.action_key, {"enabled": False, "config": {}, "safety_policy": {}})
        status = _effective_status(
            shopify_plan_ok=shopify_plan_ok,
            human_escalation_plan_ok=human_escalation_plan_ok,
            max_enabled_actions_per_agent=max_actions,
            granted=granted_set,
            definition=d,
        )
        conn_scopes = granted_set if d.requires_shopify_connection else frozenset()
        scopes_ok = _scopes_satisfied(d.required_scopes, granted_set) if d.requires_shopify_connection else True
        # UI may still show toggle; enabling blocked unless live and scopes_ok
        entry = ActionCatalogEntry(
            provider=d.provider,
            action_key=d.action_key,
            label=d.label,
            description=d.description,
            status=status,  # type: ignore[arg-type]
            required_scopes=sorted(d.required_scopes),
            connection_scopes=sorted(conn_scopes),
            scopes_satisfied=scopes_ok,
            enabled=bool(row["enabled"]),
            config=row["config"],
            safety_policy=row["safety_policy"],
        )
        out.append(entry)

    return out


async def patch_agent_action(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    action_key: str,
    payload: AgentActionPatchRequest,
) -> ActionCatalogEntry:
    await _fetch_agent_by_id(db, user_id, agent_id)
    definition = get_static_definition(action_key)
    if definition is None:
        raise AppError(code="action.unknown", message="Unknown action key", status_code=404)

    _, plan = await _ensure_default_subscription(db, user_id)
    features = plan.features or {}
    shopify_plan_ok = bool(features.get("shopify_enabled", False))
    human_escalation_plan_ok = bool(features.get("human_escalation_enabled", True))
    limits = plan_limits_dto_from_row(
        included_conversations=plan.included_conversations,
        max_agents=plan.max_agents,
        features=features,
    )
    max_enabled = limits.max_enabled_actions_per_agent

    conn = await get_connection_status(db, user_id=user_id, agent_id=agent_id)
    granted_set = frozenset((s or "").lower() for s in (conn.scopes or []))
    status = _effective_status(
        shopify_plan_ok=shopify_plan_ok,
        human_escalation_plan_ok=human_escalation_plan_ok,
        max_enabled_actions_per_agent=max_enabled,
        granted=granted_set,
        definition=definition,
    )

    await _ensure_action_rows(db, agent_id)
    current = (await _load_agent_action_map(db, agent_id)).get(
        action_key, {"enabled": False, "config": {}, "safety_policy": {}}
    )

    next_enabled = current["enabled"]
    if payload.enabled is not None:
        next_enabled = payload.enabled

    if next_enabled:
        if definition.requires_shopify_connection:
            if not shopify_plan_ok:
                raise AppError(
                    code="plan.shopify_disabled",
                    message="Shopify actions are not enabled for your plan",
                    status_code=403,
                )
            if not conn.connected:
                raise AppError(
                    code="shopify.not_connected",
                    message="Connect a Shopify store before enabling actions",
                    status_code=409,
                )
            if status != "live":
                raise AppError(
                    code="action.not_available",
                    message="This action is not available yet or required scopes are missing",
                    status_code=409,
                    details={"status": status},
                )
            if not _scopes_satisfied(definition.required_scopes, granted_set):
                raise AppError(
                    code="action.missing_scopes",
                    message="Reconnect Shopify with the required OAuth scopes",
                    status_code=409,
                    details={"required": sorted(definition.required_scopes)},
                )

            # Count enabled shopify actions if we're turning on from off
            if not current["enabled"]:
                result = await db.execute(
                    text(
                        """
                        select count(*)::int as n
                        from public.agent_actions
                        where agent_id = cast(:agent_id as uuid)
                          and enabled = true
                          and action_key like 'shopify.%'
                        """
                    ),
                    {"agent_id": str(agent_id)},
                )
                n_on = int(result.mappings().one()["n"])
                if n_on >= max_enabled:
                    raise AppError(
                        code="plan.action_limit",
                        message="Maximum enabled actions for this plan reached",
                        status_code=409,
                        details={"max_enabled_actions_per_agent": max_enabled},
                    )
        else:
            if definition.action_key == HUMAN_ACTION_KEY and (
                not human_escalation_plan_ok or max_enabled <= 0
            ):
                raise AppError(
                    code="plan.human_escalation_disabled",
                    message="Human escalation is not enabled for your plan",
                    status_code=403,
                )
            if status != "live":
                raise AppError(
                    code="action.not_available",
                    message="This action is not available yet",
                    status_code=409,
                    details={"status": status},
                )

    next_config = dict(current["config"])
    if payload.config is not None:
        next_config.update(payload.config)

    next_safety = dict(current["safety_policy"])
    if payload.safety_policy is not None:
        next_safety.update(payload.safety_policy)

    await db.execute(
        text(
            """
            update public.agent_actions
            set enabled = :enabled,
                config = cast(:config as jsonb),
                safety_policy = cast(:safety as jsonb),
                updated_at = now()
            where agent_id = cast(:agent_id as uuid) and action_key = :action_key
            """
        ),
        {
            "agent_id": str(agent_id),
            "action_key": action_key,
            "enabled": next_enabled,
            "config": json.dumps(next_config),
            "safety": json.dumps(next_safety),
        },
    )
    await db.commit()
    if action_key.startswith("shopify."):
        invalidate_shopify_actions_runtime_cache(user_id=user_id, agent_id=agent_id)

    rows = await _load_agent_action_map(db, agent_id)
    row = rows[action_key]
    status_after = _effective_status(
        shopify_plan_ok=shopify_plan_ok,
        human_escalation_plan_ok=human_escalation_plan_ok,
        max_enabled_actions_per_agent=max_enabled,
        granted=granted_set,
        definition=definition,
    )
    conn_scopes = granted_set if definition.requires_shopify_connection else frozenset()
    scopes_ok = (
        _scopes_satisfied(definition.required_scopes, granted_set)
        if definition.requires_shopify_connection
        else True
    )
    return ActionCatalogEntry(
        provider=definition.provider,
        action_key=definition.action_key,
        label=definition.label,
        description=definition.description,
        status=status_after,  # type: ignore[arg-type]
        required_scopes=sorted(definition.required_scopes),
        connection_scopes=sorted(conn_scopes),
        scopes_satisfied=scopes_ok,
        enabled=bool(row["enabled"]),
        config=row["config"],
        safety_policy=row["safety_policy"],
    )


async def list_enabled_shopify_actions_for_runtime(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Return [(action_key, config, safety_policy), ...] for enabled live Shopify actions."""
    ttl = float(get_settings().runtime_shopify_actions_cache_ttl_seconds)
    cache_key = (str(user_id), str(agent_id))
    now = time.monotonic()
    if ttl > 0:
        async with _actions_runtime_lock:
            hit = _actions_runtime_cache.get(cache_key)
            if hit is not None and (now - hit[1]) < ttl:
                log.info("runtime.shopify_actions_cache_hit", agent_id=str(agent_id))
                return [(str(t[0]), dict(t[1]), dict(t[2])) for t in hit[0]]

    await _fetch_agent_by_id(db, user_id, agent_id)
    conn = await get_connection_status(db, user_id=user_id, agent_id=agent_id)
    if not conn.connected:
        return []
    granted_set = frozenset((s or "").lower() for s in (conn.scopes or []))
    _, plan = await _ensure_default_subscription(db, user_id)
    features = plan.features or {}
    if not bool(features.get("shopify_enabled", False)):
        return []
    limits = plan_limits_dto_from_row(
        included_conversations=plan.included_conversations,
        max_agents=plan.max_agents,
        features=features,
    )
    max_n = limits.max_enabled_actions_per_agent
    if max_n <= 0:
        return []

    result = await db.execute(
        text(
            """
            select action_key, enabled, config, safety_policy
            from public.agent_actions
            where agent_id = cast(:agent_id as uuid)
              and enabled = true
              and action_key like 'shopify.%'
            """
        ),
        {"agent_id": str(agent_id)},
    )
    rows = result.mappings().all()
    out: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for r in rows:
        key = str(r["action_key"])
        definition = get_static_definition(key)
        if definition is None:
            continue
        st = _effective_status(
            shopify_plan_ok=True,
            human_escalation_plan_ok=True,
            max_enabled_actions_per_agent=max_n,
            granted=granted_set,
            definition=definition,
        )
        if st != "live":
            continue
        if not _scopes_satisfied(definition.required_scopes, granted_set):
            continue
        out.append(
            (
                key,
                dict(r["config"] or {}),
                dict(r["safety_policy"] or {}),
            )
        )

    out.sort(key=lambda t: t[0])
    if len(out) > max_n:
        out = out[:max_n]

    if ttl > 0:
        async with _actions_runtime_lock:
            _actions_runtime_cache[cache_key] = (
                tuple((k, dict(c), dict(s)) for k, c, s in out),
                time.monotonic(),
            )
    return out


async def get_human_escalation_for_runtime(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
) -> tuple[bool, dict[str, Any]]:
    """Whether human escalation is enabled and effective for this agent, plus merged config JSON."""
    await _fetch_agent_by_id(db, user_id, agent_id)
    await _ensure_action_rows(db, agent_id)
    _, plan = await _ensure_default_subscription(db, user_id)
    features = plan.features or {}
    if not bool(features.get("human_escalation_enabled", True)):
        return False, {}
    human_escalation_plan_ok = bool(features.get("human_escalation_enabled", True))
    limits = plan_limits_dto_from_row(
        included_conversations=plan.included_conversations,
        max_agents=plan.max_agents,
        features=features,
    )
    if limits.max_enabled_actions_per_agent <= 0:
        return False, {}
    conn = await get_connection_status(db, user_id=user_id, agent_id=agent_id)
    granted_set = frozenset((s or "").lower() for s in (conn.scopes or []))
    rows = await _load_agent_action_map(db, agent_id)
    row = rows.get(HUMAN_ACTION_KEY, {"enabled": False, "config": {}, "safety_policy": {}})
    if not row["enabled"]:
        return False, {}
    definition = get_static_definition(HUMAN_ACTION_KEY)
    if definition is None:
        return False, {}
    shopify_plan_ok = bool(features.get("shopify_enabled", False))
    st = _effective_status(
        shopify_plan_ok=shopify_plan_ok,
        human_escalation_plan_ok=human_escalation_plan_ok,
        max_enabled_actions_per_agent=limits.max_enabled_actions_per_agent,
        granted=granted_set,
        definition=definition,
    )
    if st != "live":
        return False, {}
    return True, dict(row["config"] or {})
