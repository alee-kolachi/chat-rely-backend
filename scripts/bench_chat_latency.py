#!/usr/bin/env python3
"""
Measure chat stream latency (time to start / first token / done).

Usage (from backend/):
  uv run python scripts/bench_chat_latency.py \\
    --base-url http://127.0.0.1:8000 \\
    --token "$SUPABASE_JWT" \\
    --agent-id "$AGENT_UUID" \\
    --message hello

Requires a valid dashboard JWT and agent id (or dev auth bypass).
Uses the same DATABASE_URL as the API (remote Supabase is fine).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def stream_chat(
    base_url: str,
    token: str,
    agent_id: str,
    message: str,
    conversation_id: str | None,
) -> dict[str, float | str | None]:
    url = f"{base_url.rstrip('/')}/api/v1/runtime/chat/stream"
    body: dict[str, object] = {"agent_id": agent_id, "message": message}
    if conversation_id:
        body["conversation_id"] = conversation_id
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    t0 = time.perf_counter()
    start_ms: float | None = None
    first_token_ms: float | None = None
    done_ms: float | None = None
    conv_id: str | None = conversation_id
    try:
        with urlopen(req, timeout=120) as resp:
            buf = b""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.strip()
                    if not text:
                        continue
                    ev = json.loads(text.decode("utf-8"))
                    now = (time.perf_counter() - t0) * 1000.0
                    typ = ev.get("type")
                    if typ == "start" and start_ms is None:
                        start_ms = now
                        conv_id = str(ev.get("conversation_id") or conv_id or "")
                    elif typ == "token" and first_token_ms is None:
                        first_token_ms = now
                    elif typ == "done":
                        done_ms = now
                        conv_id = str(ev.get("conversation_id") or conv_id or "")
    except HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {err_body[:500]}") from exc
    except URLError as exc:
        raise SystemExit(f"Request failed: {exc}") from exc

    return {
        "message": message,
        "conversation_id": conv_id,
        "start_ms": start_ms,
        "first_token_ms": first_token_ms,
        "done_ms": done_ms,
    }


def _fetch_first_agent_id(base_url: str, token: str | None) -> str:
    url = f"{base_url.rstrip('/')}/api/v1/agents"
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    agents = body if isinstance(body, list) else body.get("agents") or body.get("items") or []
    if not agents:
        raise SystemExit("No agents found for this user; create one in the dashboard first.")
    first = agents[0]
    aid = first.get("id") if isinstance(first, dict) else getattr(first, "id", None)
    if not aid:
        raise SystemExit(f"Could not parse agent id from: {first!r}")
    return str(aid)


def main() -> None:
    p = argparse.ArgumentParser(description="Benchmark runtime chat/stream latency")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument(
        "--token",
        default=None,
        help="Supabase JWT (omit if DEV_AUTH_BYPASS_ENABLED=true in development)",
    )
    p.add_argument("--agent-id", default=None, help="Defaults to first agent from GET /agents")
    p.add_argument(
        "--messages",
        nargs="*",
        default=["hello", "What is your return policy?"],
        help="Messages to send sequentially in one conversation",
    )
    args = p.parse_args()
    agent_id = args.agent_id or _fetch_first_agent_id(args.base_url, args.token)
    conv: str | None = None
    print(f"base_url={args.base_url} agent_id={agent_id} auth={'token' if args.token else 'dev-bypass'}")
    for msg in args.messages:
        row = stream_chat(args.base_url, args.token or "", agent_id, msg, conv)
        conv = str(row.get("conversation_id") or conv)
        print(
            f"  [{msg!r}] start={row['start_ms']:.0f}ms "
            f"first_token={row['first_token_ms']:.0f}ms "
            f"done={row['done_ms']:.0f}ms"
            if row["done_ms"] is not None
            else f"  [{msg!r}] incomplete"
        )
    print("Check backend logs for runtime.turn_timing (router_llm_ms should be 0).")


if __name__ == "__main__":
    main()
