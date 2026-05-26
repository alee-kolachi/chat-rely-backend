from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user, get_db
from app.domains.actions.bootstrap_schemas import (
    AgentIntegrationsBootstrapResponse,
    AgentWebsitePreview,
)
from app.domains.actions.schemas import (
    ActionCatalogEntry,
    ActionCatalogResponse,
    AgentActionPatchRequest,
)
from app.domains.actions.service import fetch_action_catalog_response, patch_agent_action
from app.domains.integrations.shopify.service import get_connection_status
from app.domains.knowledge.schemas import WebsiteSourceListItemDTO
from app.domains.knowledge.service import list_website_sources_for_agent

router = APIRouter(prefix="/agents", tags=["agent-actions"])


def _pick_agent_website_preview(sources: list[WebsiteSourceListItemDTO]) -> AgentWebsitePreview | None:
    def url_ok(row: WebsiteSourceListItemDTO) -> bool:
        u = row.source_url
        return bool(u and str(u).strip())

    picked = next((s for s in sources if url_ok(s) and s.website_mode != "individual"), None)
    if picked is None:
        picked = next((s for s in sources if url_ok(s)), None)
    if picked is None:
        return None
    return AgentWebsitePreview(
        source_url=str(picked.source_url),
        website_mode=picked.website_mode,
        title=picked.title or None,
    )


@router.get("/{agent_id}/actions/catalog", response_model=ActionCatalogResponse)
async def get_action_catalog(
    agent_id: UUID,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActionCatalogResponse:
    return await fetch_action_catalog_response(
        db, user_id=user.user_id, agent_id=agent_id
    )


@router.get("/{agent_id}/integrations/bootstrap", response_model=AgentIntegrationsBootstrapResponse)
async def get_agent_integrations_bootstrap(
    agent_id: UUID,
    include_website_preview: bool = Query(
        default=True,
        description="Whether to include computed website preview in the bootstrap payload.",
    ),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentIntegrationsBootstrapResponse:
    """Single round-trip for Playground / Actions: catalog + Shopify + website preview."""
    shopify = await get_connection_status(db, user_id=user.user_id, agent_id=agent_id)
    catalog = await fetch_action_catalog_response(
        db,
        user_id=user.user_id,
        agent_id=agent_id,
        shopify_connection=shopify,
    )
    website_preview = None
    if include_website_preview:
        website_sources = await list_website_sources_for_agent(db, user.user_id, agent_id)
        website_preview = _pick_agent_website_preview(website_sources)
    return AgentIntegrationsBootstrapResponse(
        catalog=catalog,
        shopify=shopify,
        website_preview=website_preview,
    )


@router.patch("/{agent_id}/actions/{action_key:path}", response_model=ActionCatalogEntry)
async def patch_action(
    agent_id: UUID,
    action_key: str,
    payload: AgentActionPatchRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActionCatalogEntry:
    return await patch_agent_action(
        db, user_id=user.user_id, agent_id=agent_id, action_key=action_key, payload=payload
    )
