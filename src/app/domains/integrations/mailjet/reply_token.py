"""Opaque reply routing tokens for Mailjet Parse → conversation."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from uuid import UUID


def encode_reply_token(conversation_id: UUID, user_id: UUID, secret: str) -> str:
    payload = f"{conversation_id}:{user_id}".encode()
    sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()[:16]
    # Null byte cannot appear in UUID ASCII; avoids ambiguity with binary sig bytes.
    raw = base64.urlsafe_b64encode(payload + b"\x00" + sig).decode("ascii").rstrip("=")
    return raw


def decode_reply_token(token: str, secret: str) -> tuple[UUID, UUID] | None:
    t = (token or "").strip()
    if not t:
        return None
    pad = "=" * (-len(t) % 4)
    try:
        decoded = base64.urlsafe_b64decode(t + pad)
    except (binascii.Error, ValueError):
        return None
    if b"\x00" not in decoded:
        return None
    body, sig = decoded.split(b"\x00", 1)
    if len(sig) != 16:
        return None
    try:
        text = body.decode("utf-8")
        cid_s, uid_s = text.split(":", 1)
        cid = UUID(cid_s)
        uid = UUID(uid_s)
    except (ValueError, UnicodeDecodeError):
        return None
    expect = hmac.new(secret.encode("utf-8"), f"{cid}:{uid}".encode(), hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(sig, expect):
        return None
    return cid, uid
