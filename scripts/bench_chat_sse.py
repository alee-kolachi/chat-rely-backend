#!/usr/bin/env python3
"""Measure time to first SSE status and first token on POST /api/chat/stream."""

from __future__ import annotations

import argparse
import json
import os
import time

import httpx


def parse_sse_firsts(body_iter) -> tuple[float | None, float | None]:
    buf = ""
    t_status: float | None = None
    t_token: float | None = None
    t0 = time.perf_counter()
    for chunk in body_iter:
        buf += chunk
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            if "event: status" in block and t_status is None:
                t_status = (time.perf_counter() - t0) * 1000.0
            if "event: token" in block and t_token is None:
                t_token = (time.perf_counter() - t0) * 1000.0
    return t_status, t_token


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default=os.environ.get("BENCH_BASE_URL", "http://127.0.0.1:8000"))
    p.add_argument("--token", required=True)
    p.add_argument("--agent-id", required=True)
    p.add_argument("--message", default="hello")
    args = p.parse_args()
    url = f"{args.base_url.rstrip('/')}/api/chat/stream"
    headers = {"Authorization": f"Bearer {args.token}", "Content-Type": "application/json"}
    payload = {
        "agent_id": args.agent_id,
        "message": args.message,
        "visitor_id": "bench",
    }
    with httpx.Client(timeout=120.0) as client:
        with client.stream("POST", url, headers=headers, json=payload) as res:
            res.raise_for_status()
            t_status, t_token = parse_sse_firsts(
                (res.iter_text() if hasattr(res, "iter_text") else res.iter_bytes().decode())
            )
    print(json.dumps({"first_status_ms": t_status, "first_token_ms": t_token}, indent=2))


if __name__ == "__main__":
    main()
