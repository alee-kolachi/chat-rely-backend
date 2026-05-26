"""Shopify OAuth and DB persistence for shopify_connections."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
import structlog
from sqlalchemy import String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.errors import AppError
from app.core.settings import Settings, get_settings
from app.domains.billing.customers import ensure_stripe_customer_for_user
from app.domains.integrations.shopify.oauth_state import (
    sign_oauth_state,
    validate_return_to,
    verify_oauth_state,
)
from app.domains.integrations.shopify.schemas import ShopifyConnectionStatus

log = structlog.get_logger(__name__)

# Refresh access token this many seconds before Shopify expires it.
_ACCESS_TOKEN_REFRESH_BUFFER = timedelta(seconds=120)


@dataclass
class _ShopifyConnCacheEntry:
    shop_domain: str
    access_token: str
    token_expires_at: datetime | None
    has_refresh_token: bool


_shopify_conn_cache: dict[UUID, _ShopifyConnCacheEntry] = {}
_shopify_not_connected_until: dict[UUID, float] = {}
_shopify_conn_locks: dict[UUID, asyncio.Lock] = {}


def _shopify_conn_lock(agent_id: UUID) -> asyncio.Lock:
    lk = _shopify_conn_locks.get(agent_id)
    if lk is None:
        lk = asyncio.Lock()
        _shopify_conn_locks[agent_id] = lk
    return lk


def invalidate_shopify_connection_cache(agent_id: UUID) -> None:
    """Drop cached plaintext token/decrypted metadata (call after OAuth save or disconnect)."""
    _shopify_conn_cache.pop(agent_id, None)
    _shopify_not_connected_until.pop(agent_id, None)


def is_shopify_disconnected_cached(agent_id: UUID) -> bool:
    """True when a recent probe found no connected store (skip DB on hot path)."""
    neg_ttl = float(get_settings().runtime_shopify_disconnected_cache_ttl_seconds)
    if neg_ttl <= 0:
        return False
    neg_until = _shopify_not_connected_until.get(agent_id)
    return neg_until is not None and time.monotonic() < neg_until


def mark_shopify_disconnected_cached(agent_id: UUID) -> None:
    """Prime negative cache without a DB round-trip (startup warmup)."""
    neg_ttl = float(get_settings().runtime_shopify_disconnected_cache_ttl_seconds)
    if neg_ttl > 0:
        _shopify_not_connected_until[agent_id] = time.monotonic() + neg_ttl


def get_cached_shopify_connection(agent_id: UUID) -> tuple[str, str] | None:
    """In-process hit for connected store (skip DB when token still valid)."""
    cached = _shopify_conn_cache.get(agent_id)
    if cached is None:
        return None
    if _needs_access_refresh(cached.token_expires_at, has_refresh=cached.has_refresh_token):
        return None
    return cached.shop_domain, cached.access_token


def normalize_shop_domain(shop: str) -> str:
    s = (shop or "").strip().lower()
    if not s:
        raise AppError(code="shopify.invalid_shop", message="Shop domain is required", status_code=400)
    s = s.replace("https://", "").replace("http://", "").split("/")[0]
    if not s.endswith(".myshopify.com"):
        if "." in s:
            raise AppError(
                code="shopify.invalid_shop",
                message="Use your-store.myshopify.com or your-store subdomain only",
                status_code=400,
            )
        s = f"{s}.myshopify.com"
    return s


async def _ensure_agent_owned(db: AsyncSession, user_id: UUID, agent_id: UUID) -> None:
    result = await db.execute(
        text("select 1 from public.agents where id = :agent_id and user_id = :user_id"),
        {"agent_id": str(agent_id), "user_id": str(user_id)},
    )
    if result.scalar_one_or_none() is None:
        raise AppError(code="agent.not_found", message="Agent not found", status_code=404)


def _oauth_redirect_uri(settings: Settings) -> str:
    base = str(settings.public_api_base_url).rstrip("/")
    return f"{base}/api/v1/integrations/shopify/oauth/callback"


def _scopes_list(settings: Settings) -> list[str]:
    raw = (settings.shopify_scopes or "").replace(" ", "")
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


async def build_authorization_url(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    shop: str,
    return_to: str | None = None,
) -> str:
    settings = get_settings()
    if not settings.shopify_api_key or not settings.shopify_api_secret:
        raise AppError(
            code="shopify.not_configured",
            message="Shopify API credentials are not configured on the server",
            status_code=503,
        )
    if not settings.integration_oauth_state_secret or not settings.integration_token_fernet_key:
        raise AppError(
            code="shopify.not_configured",
            message="Integration encryption or OAuth state secret is not configured",
            status_code=503,
        )
    await _ensure_agent_owned(db, user_id, agent_id)
    shop_domain = normalize_shop_domain(shop)
    validated_return = validate_return_to(return_to) if return_to else None
    nonce = secrets.token_urlsafe(16)
    state = sign_oauth_state(
        secret=settings.integration_oauth_state_secret,
        user_id=user_id,
        agent_id=agent_id,
        nonce=nonce,
        return_to=validated_return,
    )
    redirect_uri = _oauth_redirect_uri(settings)
    scope = ",".join(_scopes_list(settings))
    query = {
        "client_id": settings.shopify_api_key,
        "scope": scope,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    q = urlencode(query, safe="")
    return f"https://{shop_domain}/admin/oauth/authorize?{q}"


def _parse_scope_string(scope: str | None) -> list[str]:
    if not scope:
        return []
    parts = scope.replace(",", " ").split()
    return sorted({p.strip().lower() for p in parts if p.strip()})


def _normalize_scope_from_oauth_response(data: dict[str, Any]) -> str | None:
    """
    Shopify usually returns `scope` as a comma-separated string; some responses use a list
    or omit `scope` entirely. Never return an empty string here — caller may fall back to
    configured SHOPIFY_SCOPES after a successful token exchange.
    """
    raw = data.get("scope")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw if str(x).strip()]
        if parts:
            return ",".join(parts)
    alt = data.get("scopes")
    if isinstance(alt, str) and alt.strip():
        return alt.strip()
    if isinstance(alt, list):
        parts = [str(x).strip() for x in alt if str(x).strip()]
        if parts:
            return ",".join(parts)
    return None


async def exchange_code_for_token(*, shop_domain: str, code: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.shopify_api_key or not settings.shopify_api_secret:
        raise AppError(
            code="shopify.not_configured",
            message="Shopify API credentials are not configured on the server",
            status_code=503,
        )
    url = f"https://{shop_domain}/admin/oauth/access_token"
    # Expiring offline tokens (refresh_token + expires_in). Non-expiring tokens are rejected by Admin API.
    payload = {
        "client_id": settings.shopify_api_key,
        "client_secret": settings.shopify_api_secret,
        "code": code,
        "expiring": "1",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            data=payload,
            headers={"Accept": "application/json"},
        )
    if response.status_code != 200:
        raise AppError(
            code="shopify.token_exchange_failed",
            message="Failed to exchange authorization code for access token",
            status_code=502,
            details={"status": response.status_code, "body": response.text[:500]},
        )
    return response.json()


async def refresh_shopify_tokens(*, shop_domain: str, refresh_token: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.shopify_api_key or not settings.shopify_api_secret:
        raise AppError(
            code="shopify.not_configured",
            message="Shopify API credentials are not configured on the server",
            status_code=503,
        )
    url = f"https://{shop_domain}/admin/oauth/access_token"
    payload = {
        "client_id": settings.shopify_api_key,
        "client_secret": settings.shopify_api_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            data=payload,
            headers={"Accept": "application/json"},
        )
    if response.status_code != 200:
        raise AppError(
            code="shopify.token_refresh_failed",
            message="Failed to refresh Shopify access token. Reconnect Shopify.",
            status_code=502,
            details={"status": response.status_code, "body": response.text[:500]},
        )
    return response.json()


def _token_expires_at_from_response(data: dict[str, Any]) -> datetime | None:
    raw = data.get("expires_in")
    if raw is None:
        return None
    try:
        sec = int(raw)
    except (TypeError, ValueError):
        return None
    return datetime.now(tz=UTC) + timedelta(seconds=sec)


def _metadata_from_token_response(data: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    rt_exp = data.get("refresh_token_expires_in")
    if rt_exp is not None:
        try:
            sec = int(rt_exp)
            meta["refresh_token_expires_at"] = (datetime.now(tz=UTC) + timedelta(seconds=sec)).isoformat()
        except (TypeError, ValueError):
            pass
    return meta


async def save_connection(
    db: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    shop_domain: str,
    access_token: str,
    scope_str: str | None,
    token_response: dict[str, Any] | None = None,
) -> None:
    """Encrypt token(s) and upsert shopify_connections for this agent."""
    settings = get_settings()
    fernet_key = settings.integration_token_fernet_key
    if not fernet_key:
        raise AppError(code="shopify.not_configured", message="Token encryption not configured", status_code=503)
    await _ensure_agent_owned(db, user_id, agent_id)
    enc = encrypt_secret(access_token, fernet_key)
    refresh_enc: str | None = None
    if token_response and token_response.get("refresh_token"):
        refresh_enc = encrypt_secret(str(token_response["refresh_token"]), fernet_key)

    expires_at: datetime | None = None
    meta: dict[str, Any] = {}
    if token_response is not None:
        expires_at = _token_expires_at_from_response(token_response)
        meta = _metadata_from_token_response(token_response)
    scopes = _parse_scope_string(scope_str)
    stmt = (
        text(
            """
            insert into public.shopify_connections (
              agent_id, shop_domain, access_token_encrypted, refresh_token_encrypted, token_expires_at,
              scopes, status, metadata, last_synced_at
            ) values (
              cast(:agent_id as uuid), :shop_domain, :enc, :refresh_enc, :token_expires_at,
              :scopes, 'connected', cast(:metadata as jsonb), now()
            )
            on conflict (agent_id) do update set
              shop_domain = excluded.shop_domain,
              access_token_encrypted = excluded.access_token_encrypted,
              refresh_token_encrypted = excluded.refresh_token_encrypted,
              token_expires_at = excluded.token_expires_at,
              scopes = excluded.scopes,
              status = 'connected',
              metadata = excluded.metadata,
              last_synced_at = now(),
              updated_at = now()
            """
        )
        .bindparams(bindparam("scopes", type_=ARRAY(String())))
    )
    await db.execute(
        stmt,
        {
            "agent_id": str(agent_id),
            "shop_domain": shop_domain,
            "enc": enc,
            "refresh_enc": refresh_enc,
            "token_expires_at": expires_at,
            "scopes": scopes,
            "metadata": json.dumps(meta),
        },
    )


async def handle_oauth_callback(
    db: AsyncSession,
    *,
    code: str,
    state: str,
    shop: str,
) -> tuple[UUID, UUID]:
    """Validate state, exchange code, save connection. Returns (user_id, agent_id)."""
    settings = get_settings()
    if not settings.integration_oauth_state_secret:
        raise AppError(code="shopify.not_configured", message="OAuth state secret not configured", status_code=503)
    try:
        payload = verify_oauth_state(secret=settings.integration_oauth_state_secret, state=state)
    except ValueError as exc:
        raise AppError(
            code="shopify.invalid_state", message="Invalid or expired OAuth state", status_code=400
        ) from exc
    user_id = UUID(str(payload["u"]))
    agent_id = UUID(str(payload["a"]))
    shop_domain = normalize_shop_domain(shop)
    data = await exchange_code_for_token(shop_domain=shop_domain, code=code)
    token = str(data.get("access_token") or "")
    if not token:
        raise AppError(code="shopify.token_missing", message="No access token in Shopify response", status_code=502)
    scope_str = _normalize_scope_from_oauth_response(data)
    if not scope_str:
        # Successful exchange but no scope field — persist what we requested on /authorize
        # so the Actions catalog can evaluate required_scopes (avoids empty granted_set).
        scope_str = ",".join(_scopes_list(settings))
    await save_connection(
        db,
        user_id=user_id,
        agent_id=agent_id,
        shop_domain=shop_domain,
        access_token=token,
        scope_str=scope_str,
        token_response=data,
    )
    try:
        await ensure_stripe_customer_for_user(db, user_id=user_id, shop_domain=shop_domain)
    except Exception:
        log.warning("shopify.stripe_customer_failed", user_id=str(user_id), exc_info=True)
    await db.commit()
    invalidate_shopify_connection_cache(agent_id)
    from app.domains.actions.service import invalidate_shopify_actions_runtime_cache

    invalidate_shopify_actions_runtime_cache(user_id=user_id, agent_id=agent_id)
    return user_id, agent_id


async def delete_connection(db: AsyncSession, *, user_id: UUID, agent_id: UUID) -> None:
    await _ensure_agent_owned(db, user_id, agent_id)
    await db.execute(
        text("delete from public.shopify_connections where agent_id = cast(:agent_id as uuid)"),
        {"agent_id": str(agent_id)},
    )
    await db.commit()
    invalidate_shopify_connection_cache(agent_id)
    from app.domains.actions.service import invalidate_shopify_actions_runtime_cache

    invalidate_shopify_actions_runtime_cache(user_id=user_id, agent_id=agent_id)


async def get_connection_status(
    db: AsyncSession, *, user_id: UUID, agent_id: UUID
) -> ShopifyConnectionStatus:
    await _ensure_agent_owned(db, user_id, agent_id)
    result = await db.execute(
        text(
            """
            select shop_domain, scopes, status, last_synced_at
            from public.shopify_connections
            where agent_id = cast(:agent_id as uuid)
            """
        ),
        {"agent_id": str(agent_id)},
    )
    row = result.mappings().first()
    if not row:
        return ShopifyConnectionStatus(connected=False, status="disconnected")
    return ShopifyConnectionStatus(
        connected=row["status"] == "connected",
        shop_domain=str(row["shop_domain"]),
        scopes=list(row["scopes"] or []),
        status=str(row["status"]),
        last_synced_at=row["last_synced_at"].isoformat() if row["last_synced_at"] else None,
    )


def decrypt_shopify_access_token_encrypted(ciphertext: str) -> str:
    settings = get_settings()
    key = settings.integration_token_fernet_key
    if not key:
        raise AppError(code="shopify.not_configured", message="Token encryption not configured", status_code=503)
    return decrypt_secret(ciphertext, key)


def _scopes_array_to_scope_str(scopes: list[str] | None) -> str | None:
    if not scopes:
        return None
    return ",".join(scopes)


def _needs_access_refresh(token_expires_at: datetime | None, *, has_refresh: bool) -> bool:
    if not has_refresh or token_expires_at is None:
        return False
    dt = token_expires_at
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=UTC)
    return dt <= datetime.now(tz=UTC) + _ACCESS_TOKEN_REFRESH_BUFFER


async def load_shopify_connection_for_agent(
    db: AsyncSession, *, user_id: UUID, agent_id: UUID
) -> tuple[str, str] | None:
    """Returns (shop_domain, plaintext_access_token) if connected. Refreshes expiring tokens when due.

    Reuses an in-process cache so repeated chat turns skip DB/decrypt (and skip OAuth refresh POST)
    while ``token_expires_at`` is still comfortably in the future.
    """
    lk = _shopify_conn_lock(agent_id)
    async with lk:
        cached = _shopify_conn_cache.get(agent_id)
        if cached is not None and not _needs_access_refresh(
            cached.token_expires_at, has_refresh=cached.has_refresh_token
        ):
            log.info(
                "shopify.connection_cache_hit",
                agent_id=str(agent_id),
                expires_at=cached.token_expires_at.isoformat() if cached.token_expires_at else None,
            )
            return cached.shop_domain, cached.access_token

        neg_ttl = float(get_settings().runtime_shopify_disconnected_cache_ttl_seconds)
        now_mono = time.monotonic()
        neg_until = _shopify_not_connected_until.get(agent_id)
        if neg_ttl > 0 and neg_until is not None and now_mono < neg_until:
            log.info("shopify.connection_negative_cache_hit", agent_id=str(agent_id))
            return None

        await _ensure_agent_owned(db, user_id, agent_id)
        result = await db.execute(
            text(
                """
                select
                  shop_domain,
                  access_token_encrypted,
                  refresh_token_encrypted,
                  token_expires_at,
                  scopes,
                  status
                from public.shopify_connections
                where agent_id = cast(:agent_id as uuid)
                """
            ),
            {"agent_id": str(agent_id)},
        )
        row = result.mappings().first()
        if not row or row["status"] != "connected":
            _shopify_conn_cache.pop(agent_id, None)
            mark_shopify_disconnected_cached(agent_id)
            return None
        shop_domain = str(row["shop_domain"])
        token = decrypt_shopify_access_token_encrypted(str(row["access_token_encrypted"]))
        refresh_cipher = row.get("refresh_token_encrypted")
        refresh_plain = (
            decrypt_shopify_access_token_encrypted(str(refresh_cipher)) if refresh_cipher else None
        )
        expires_at: datetime | None = row.get("token_expires_at")
        if isinstance(expires_at, datetime) and getattr(expires_at, "tzinfo", None) is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        has_refresh = bool(refresh_plain)

        if _needs_access_refresh(expires_at, has_refresh=has_refresh) and refresh_plain:
            try:
                refreshed = await refresh_shopify_tokens(
                    shop_domain=shop_domain, refresh_token=refresh_plain
                )
                new_access = str(refreshed.get("access_token") or "")
                if not new_access:
                    raise AppError(
                        code="shopify.token_missing",
                        message="No access token after refresh",
                        status_code=502,
                    )
                scope_str = _normalize_scope_from_oauth_response(refreshed) or _scopes_array_to_scope_str(
                    list(row["scopes"] or [])
                )
                await save_connection(
                    db,
                    user_id=user_id,
                    agent_id=agent_id,
                    shop_domain=shop_domain,
                    access_token=new_access,
                    scope_str=scope_str,
                    token_response=refreshed,
                )
                await db.commit()
                token = new_access
                expires_at = _token_expires_at_from_response(refreshed)
                if expires_at is None:
                    expires_at = row.get("token_expires_at")
                    if isinstance(expires_at, datetime) and getattr(expires_at, "tzinfo", None) is None:
                        expires_at = expires_at.replace(tzinfo=UTC)
                has_refresh = bool(refreshed.get("refresh_token")) or has_refresh
            except AppError:
                await db.rollback()
                raise

        _shopify_not_connected_until.pop(agent_id, None)
        _shopify_conn_cache[agent_id] = _ShopifyConnCacheEntry(
            shop_domain=shop_domain,
            access_token=token,
            token_expires_at=expires_at,
            has_refresh_token=has_refresh,
        )
        return shop_domain, token
