"""Startup warmup for runtime latency caches (Shopify, KB index probes)."""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import text

from app.core.settings import get_settings
from app.db.session import get_session_factory
from app.domains.integrations.shopify.service import mark_shopify_disconnected_cached
from app.domains.runtime.shopify_runtime_warmup import (
    warm_shopify_disconnected_caches,
    warm_shopify_runtime_caches,
)

log = structlog.get_logger("runtime.cache_warmup")


async def warm_kb_index_caches() -> None:
    """Prime ``_agent_has_indexed_knowledge`` for recent agents (avoids per-turn exists query)."""
    lim = max(0, int(get_settings().runtime_shopify_cache_warm_max_agents))
    if lim == 0:
        return
    sf = get_session_factory()
    try:
        async with sf() as s:
            result = await s.execute(
                text(
                    """
                    select id as agent_id
                    from public.agents
                    where archived_at is null
                    order by updated_at desc nulls last, created_at desc
                    limit :lim
                    """
                ),
                {"lim": lim},
            )
            agent_ids = [UUID(str(r["agent_id"])) for r in result.mappings().all()]
    except Exception as exc:
        log.warning("runtime.kb_warmup_query_failed", error=str(exc))
        return
    from app.domains.runtime.service import _agent_has_indexed_knowledge

    for aid in agent_ids:
        try:
            await _agent_has_indexed_knowledge(aid)
        except Exception as exc:
            log.warning("runtime.kb_warmup_agent_failed", agent_id=str(aid), error=str(exc))
    log.info("runtime.kb_warmup_done", primed=len(agent_ids))


async def warm_all_runtime_caches() -> None:
    await warm_shopify_runtime_caches()
    await warm_shopify_disconnected_caches()
    await warm_kb_index_caches()
