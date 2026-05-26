"""Resolve safe return URLs for Stripe Checkout and Customer Portal."""

from __future__ import annotations

from urllib.parse import urlparse

from app.core.settings import Settings


def _normalize_origin(value: str) -> str:
    return value.strip().rstrip("/")


def _origin_allowed(settings: Settings, origin: str) -> bool:
    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False

    default = _normalize_origin(settings.billing_app_base_url)
    default_host = urlparse(default).hostname or ""

    if origin == default:
        return True

    # Local dev: allow localhost / 127.0.0.1 on any port when default is also loopback.
    host = parsed.hostname or ""
    if host in ("localhost", "127.0.0.1") and default_host in ("localhost", "127.0.0.1"):
        return True

    extra = (getattr(settings, "billing_app_extra_origins", None) or "").strip()
    if extra:
        for part in extra.split(","):
            candidate = _normalize_origin(part)
            if candidate and origin == candidate:
                return True

    return False


def resolve_billing_app_base_url(
    settings: Settings,
    *,
    return_origin: str | None = None,
    request_origin: str | None = None,
) -> str:
    """
    Pick the app origin for Stripe return_url.

    Prefers the browser origin from the client so redirects land on the same host
    the user is actually using (not a stale BILLING_APP_BASE_URL).
    """
    default = _normalize_origin(settings.billing_app_base_url)

    ro = _normalize_origin(return_origin) if return_origin else None
    req = _normalize_origin(request_origin) if request_origin else None
    if ro and req and ro == req and ro.startswith(("http://", "https://")):
        return ro

    for raw in (return_origin, request_origin):
        if not raw:
            continue
        candidate = _normalize_origin(raw)
        if not candidate.startswith(("http://", "https://")):
            continue
        if _origin_allowed(settings, candidate):
            return candidate

    return default
