"""Signed OAuth state for Shopify install flow (HMAC over base64url JSON)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from uuid import UUID

STATE_MAX_AGE_SEC = 3600
RETURN_TO_MAX_LEN = 512


def validate_return_to(raw: object | None) -> str | None:
    """Safe relative path for post-OAuth redirect (same origin, path-only)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or len(s) > RETURN_TO_MAX_LEN:
        return None
    if not s.startswith("/"):
        return None
    if s.startswith("//") or "\n" in s or "\r" in s:
        return None
    if "://" in s:
        return None
    return s


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64url_decode(seg: str) -> bytes:
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + pad)


def sign_oauth_state(
    *,
    secret: str,
    user_id: UUID,
    agent_id: UUID,
    nonce: str,
    return_to: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "u": str(user_id),
        "a": str(agent_id),
        "n": nonce,
        "t": int(time.time()),
    }
    if return_to:
        payload["r"] = return_to
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    body = _b64url_encode(raw)
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_oauth_state(*, secret: str, state: str) -> dict[str, Any]:
    parts = state.split(".", 1)
    if len(parts) != 2:
        raise ValueError("invalid state format")
    body, sig = parts
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ValueError("invalid state signature")
    payload = json.loads(_b64url_decode(body).decode())
    ts = int(payload.get("t", 0))
    if time.time() - ts > STATE_MAX_AGE_SEC:
        raise ValueError("state expired")
    return payload
