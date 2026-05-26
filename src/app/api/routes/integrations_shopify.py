from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user, get_db
from app.core.errors import AppError
from app.core.settings import get_settings
from app.domains.integrations.shopify.oauth_state import validate_return_to, verify_oauth_state
from app.domains.integrations.shopify.schemas import (
    ShopifyConnectionStatus,
    ShopifyOAuthStartResponse,
)
from app.domains.integrations.shopify.service import (
    build_authorization_url,
    delete_connection,
    get_connection_status,
    handle_oauth_callback,
)

router = APIRouter(prefix="/integrations/shopify", tags=["integrations-shopify"])


def _frontend_origin() -> str:
    settings = get_settings()
    p = urlparse(settings.shopify_oauth_success_redirect)
    return f"{p.scheme}://{p.netloc}".rstrip("/")


def _merge_query(url: str, extra: dict[str, str]) -> str:
    p = urlparse(url)
    pairs = dict(parse_qsl(p.query, keep_blank_values=True))
    pairs.update(extra)
    new_q = urlencode(pairs)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, new_q, p.fragment))


def _success_redirect_url(return_to: str | None) -> str:
    settings = get_settings()
    if return_to:
        return _merge_query(f"{_frontend_origin()}{return_to}", {"shopify": "connected"})
    base = settings.shopify_oauth_success_redirect.split("?")[0].rstrip("/")
    return f"{base}?shopify=connected"


def _failure_redirect_url(code: str, message: str, return_to: str | None) -> str:
    settings = get_settings()
    params = {"shopify": "error", "code": code, "message": message[:200]}
    if return_to:
        return _merge_query(f"{_frontend_origin()}{return_to}", params)
    base = settings.shopify_oauth_success_redirect.split("?")[0].rstrip("/")
    return f"{base}?{urlencode(params)}"


@router.get("/oauth/start", response_model=ShopifyOAuthStartResponse)
async def shopify_oauth_start(
    agent_id: UUID = Query(),
    shop: str = Query(min_length=1, description="Store subdomain or myshopify.com domain"),
    return_to: str | None = Query(
        default=None,
        description="Optional path (and query) on the app origin to open after OAuth, e.g. /onboarding/connection?agentId=…",
    ),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShopifyOAuthStartResponse:
    url = await build_authorization_url(
        db,
        user_id=user.user_id,
        agent_id=agent_id,
        shop=shop,
        return_to=return_to,
    )
    return ShopifyOAuthStartResponse(authorization_url=url)


@router.get("/oauth/callback")
async def shopify_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    shop: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Public callback from Shopify (no bearer token). State carries user and agent identity."""
    settings = get_settings()
    if not code or not state or not shop:
        return RedirectResponse(
            _failure_redirect_url("missing_params", "Missing code, state, or shop", None),
            status_code=302,
        )

    return_to: str | None = None
    try:
        payload = verify_oauth_state(secret=settings.integration_oauth_state_secret, state=state)
        return_to = validate_return_to(payload.get("r"))
    except ValueError:
        return RedirectResponse(
            _failure_redirect_url("invalid_state", "Invalid or expired OAuth state", None),
            status_code=302,
        )

    try:
        await handle_oauth_callback(db, code=code, state=state, shop=shop)
    except AppError as exc:
        await db.rollback()
        return RedirectResponse(
            _failure_redirect_url(exc.code, exc.message or "OAuth failed", return_to),
            status_code=302,
        )
    except Exception as exc:
        await db.rollback()
        return RedirectResponse(
            _failure_redirect_url("oauth_failed", str(exc)[:200], return_to),
            status_code=302,
        )
    return RedirectResponse(_success_redirect_url(return_to), status_code=302)


@router.get("", response_model=ShopifyConnectionStatus)
async def shopify_connection_get(
    agent_id: UUID = Query(),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShopifyConnectionStatus:
    return await get_connection_status(db, user_id=user.user_id, agent_id=agent_id)


@router.delete("", response_model=ShopifyConnectionStatus)
async def shopify_connection_delete(
    agent_id: UUID = Query(),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShopifyConnectionStatus:
    await delete_connection(db, user_id=user.user_id, agent_id=agent_id)
    return await get_connection_status(db, user_id=user.user_id, agent_id=agent_id)
