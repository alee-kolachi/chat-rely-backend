"""In-process sliding-window rate limiting (single-replica MVP; use Redis when horizontally scaled)."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import MutableMapping
from uuid import UUID

from starlette.requests import Request

from app.core.auth_state import get_token_verifier
from app.core.errors import AuthError


class SlidingWindowLimiter:
    """Fixed-window counter per key with monotonic timestamps."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._hits: MutableMapping[str, list[float]] = defaultdict(list)

    async def check(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds). retry_after is 0 when allowed."""
        if limit <= 0 or window_seconds <= 0:
            return True, 0

        now = time.monotonic()
        window_start = now - float(window_seconds)

        async with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] < window_start:
                bucket.pop(0)
            if len(bucket) >= limit:
                oldest = bucket[0]
                retry_after = max(1, int(window_seconds - (now - oldest)) + 1)
                return False, retry_after
            bucket.append(now)
            return True, 0

    async def reset_key(self, key: str) -> None:
        async with self._lock:
            self._hits.pop(key, None)

    async def clear_all(self) -> None:
        async with self._lock:
            self._hits.clear()


# Global limiter shared by HTTP middleware and visitor chat checks.
_limiter = SlidingWindowLimiter()


def get_limiter() -> SlidingWindowLimiter:
    return _limiter


def resolve_client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def optional_user_id_from_request(request: Request) -> UUID | None:
    auth = (request.headers.get("authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    try:
        claims = get_token_verifier().verify_token(token)
        sub = claims.get("sub")
        if sub is None:
            return None
        return UUID(str(sub))
    except (AuthError, TypeError, ValueError):
        return None


def build_rate_limit_key(*, scope: str, tier: str, identifier: str) -> str:
    return f"{scope}:{identifier}:{tier}"


async def check_rate_keys(
    keys: list[str],
    *,
    limit: int,
    window_seconds: int = 60,
) -> tuple[bool, int]:
    """All keys must pass; returns first failure retry_after."""
    limiter = get_limiter()
    for key in keys:
        allowed, retry_after = await limiter.check(key, limit=limit, window_seconds=window_seconds)
        if not allowed:
            return False, retry_after
    return True, 0
