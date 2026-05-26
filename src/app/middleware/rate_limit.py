"""HTTP rate limiting by route tier, client IP, and optional authenticated user."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.errors import RateLimitError, error_response
from app.core.rate_limit import (
    build_rate_limit_key,
    check_rate_keys,
    optional_user_id_from_request,
    resolve_client_ip,
)
from app.core.settings import get_settings

RateLimitTier = Literal["default", "chat", "knowledge", "knowledge_read", "admin", "webhook", "public"]

_TIER_LIMIT_ATTR: dict[RateLimitTier, str] = {
    "default": "rate_limit_default_per_minute",
    "chat": "rate_limit_chat_per_minute",
    "knowledge": "rate_limit_knowledge_per_minute",
    "knowledge_read": "rate_limit_knowledge_read_per_minute",
    "admin": "rate_limit_admin_per_minute",
    "webhook": "rate_limit_webhook_per_minute",
    "public": "rate_limit_public_per_minute",
}

_KNOWLEDGE_PREFIXES = (
    "/api/v1/knowledge/website/",
    "/api/v1/knowledge/sources/",
    "/api/v1/knowledge/files/upload",
    "/api/v1/knowledge/snippets",
    "/api/v1/knowledge/qa",
    "/api/v1/onboarding/website",
)


def resolve_rate_limit_tier(path: str, method: str) -> RateLimitTier:
    if path.startswith("/api/chat/"):
        return "chat"
    if any(path.startswith(p) for p in _KNOWLEDGE_PREFIXES):
        # Website crawl/indexing runs in indexing_worker (no HTTP). These limits cover dashboard
        # API calls only: starting a crawl (POST) and polling sources/pages (GET).
        if method in ("GET", "HEAD"):
            return "knowledge_read"
        return "knowledge"
    if path.startswith("/api/v1/admin/"):
        return "admin"
    if (
        path.startswith("/api/v1/webhooks/")
        or path.startswith("/api/v1/integrations/mailjet/inbound")
        or path == "/api/v1/integrations/shopify/oauth/callback"
    ):
        return "webhook"
    if path.startswith("/api/v1/public/") or path.startswith("/api/v1/plans/public"):
        return "public"
    return "default"


def _is_exempt(path: str, method: str) -> bool:
    if method == "OPTIONS":
        return True
    if path.startswith("/api/v1/health/"):
        return True
    return False


def _limit_for_tier(tier: RateLimitTier) -> int:
    settings = get_settings()
    attr = _TIER_LIMIT_ATTR[tier]
    return max(1, int(getattr(settings, attr)))


async def enforce_http_rate_limit(request: Request) -> JSONResponse | None:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return None

    path = request.url.path
    method = request.method.upper()
    if _is_exempt(path, method):
        return None

    tier = resolve_rate_limit_tier(path, method)
    limit = _limit_for_tier(tier)
    window_seconds = 60

    ip = resolve_client_ip(request)
    keys = [build_rate_limit_key(scope="ip", tier=tier, identifier=ip)]

    user_id = optional_user_id_from_request(request)
    if user_id is not None:
        keys.append(build_rate_limit_key(scope="user", tier=tier, identifier=str(user_id)))

    allowed, retry_after = await check_rate_keys(keys, limit=limit, window_seconds=window_seconds)
    if allowed:
        return None

    exc = RateLimitError(retry_after_seconds=retry_after)
    return error_response(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        details=exc.details,
        request_id=getattr(request.state, "request_id", None),
        headers={"Retry-After": str(max(1, retry_after))},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not getattr(request.state, "request_id", None):
            request.state.request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        blocked = await enforce_http_rate_limit(request)
        if blocked is not None:
            blocked.headers["x-request-id"] = str(request.state.request_id)
            return blocked
        return await call_next(request)
