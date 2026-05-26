#!/usr/bin/env python3
"""
Benchmark stream_chat (SSE) in-process (uses backend/.env: remote Supabase + OpenAI).

Works with hosted Supabase — no local Postgres required. Reads DATABASE_URL from .env.

Usage (from backend/):
  uv run python scripts/bench_chat_inprocess.py
  uv run python scripts/bench_chat_inprocess.py --user-id UUID --agent-id UUID
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from uuid import UUID

from sqlalchemy import text

from app.core.settings import get_settings
from app.db.engine import init_engine
from app.db.session import get_session_factory, init_session_factory
from app.domains.runtime.schemas import RuntimeChatRequest
from app.agent.service import stream_chat
from app.domains.runtime.runtime_cache_warmup import warm_all_runtime_caches


async def _resolve_ids(
    *,
    user_id: UUID | None,
    agent_id: UUID | None,
) -> tuple[UUID, UUID]:
    sf = get_session_factory()
    async with sf() as db:
        if user_id is None:
            row = (
                await db.execute(
                    text(
                        """
                        select user_id, id as agent_id
                        from public.agents
                        where archived_at is null
                        order by created_at desc
                        limit 1
                        """
                    )
                )
            ).mappings().first()
            if not row:
                raise SystemExit("No agents in database.")
            user_id = UUID(str(row["user_id"]))
            agent_id = UUID(str(row["agent_id"])) if agent_id is None else agent_id
        elif agent_id is None:
            row = (
                await db.execute(
                    text(
                        """
                        select id as agent_id
                        from public.agents
                        where user_id = cast(:uid as uuid)
                          and archived_at is null
                        order by created_at desc
                        limit 1
                        """
                    ),
                    {"uid": str(user_id)},
                )
            ).mappings().first()
            if not row:
                raise SystemExit(f"No agents for user {user_id}")
            agent_id = UUID(str(row["agent_id"]))
    assert user_id is not None and agent_id is not None
    return user_id, agent_id


async def _bench_message(
    *,
    user_id: UUID,
    agent_id: UUID,
    message: str,
    conversation_id: UUID | None,
) -> dict[str, float | str | None]:
    sf = get_session_factory()
    payload = RuntimeChatRequest(
        agent_id=agent_id,
        message=message,
        conversation_id=conversation_id,
        visitor_id="bench-visitor",
    )
    t0 = time.perf_counter()
    first_status_ms: float | None = None
    first_token_ms: float | None = None
    done_ms: float | None = None
    out_conv: str | None = str(conversation_id) if conversation_id else None

    async for frame in stream_chat(user_id, payload):
        now = (time.perf_counter() - t0) * 1000.0
        if frame.startswith("event: status") and first_status_ms is None:
            first_status_ms = now
        elif frame.startswith("event: token") and first_token_ms is None:
            first_token_ms = now
        elif frame.startswith("event: done"):
            done_ms = now
            for line in frame.split("\n"):
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    out_conv = str(data.get("conversation_id") or out_conv)
                    break

    return {
        "message": message,
        "conversation_id": out_conv,
        "first_token_ms": first_token_ms,
        "done_ms": done_ms,
    }


async def _main_async(
    user_id: UUID | None,
    agent_id: UUID | None,
    messages: list[str],
    *,
    fresh_conversation: bool,
) -> None:
    uid, aid = await _resolve_ids(user_id=user_id, agent_id=agent_id)
    await warm_all_runtime_caches()
    print(f"user_id={uid} agent_id={aid}")
    conv: UUID | None = None
    for msg in messages:
        if fresh_conversation:
            conv = None
        row = await _bench_message(
            user_id=uid, agent_id=aid, message=msg, conversation_id=conv
        )
        if row.get("conversation_id"):
            conv = UUID(str(row["conversation_id"]))
        ft = row.get("first_token_ms")
        dn = row.get("done_ms")
        print(
            f"  [{msg!r}] first_token={ft:.0f}ms done={dn:.0f}ms"
            if ft is not None and dn is not None
            else f"  [{msg!r}] first_token={ft} done={dn}"
        )
    print("See logs for runtime.turn_timing (router_llm_ms should be 0).")


def main() -> None:
    init_engine(get_settings())
    init_session_factory()
    p = argparse.ArgumentParser()
    p.add_argument("--user-id", default=None)
    p.add_argument("--agent-id", default=None)
    p.add_argument("--messages", nargs="*", default=["hello", "What is your return policy?"])
    p.add_argument(
        "--fresh-conversation",
        action="store_true",
        help="New conversation per message (fair hello latency; avoids huge history)",
    )
    args = p.parse_args()
    uid = UUID(args.user_id) if args.user_id else None
    aid = UUID(args.agent_id) if args.agent_id else None
    asyncio.run(
        _main_async(uid, aid, list(args.messages), fresh_conversation=bool(args.fresh_conversation))
    )


if __name__ == "__main__":
    main()
