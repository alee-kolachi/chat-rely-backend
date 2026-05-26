from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user, get_db
from app.domains.knowledge.schemas import (
    SourceIndexResponse,
    TextSnippetCreateRequest,
    TextSnippetDetailDTO,
    TextSnippetsListResponse,
    TextSnippetUpdateRequest,
)
from app.domains.knowledge.service import (
    create_and_index_text_snippet,
    delete_text_snippet_source,
    get_text_snippet_detail,
    list_text_snippet_sources_for_agent,
    update_text_snippet_source,
)

router = APIRouter(prefix="/knowledge/snippets", tags=["knowledge-snippets"])


@router.post("", response_model=SourceIndexResponse)
async def create_snippet_route(
    payload: TextSnippetCreateRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SourceIndexResponse:
    source, job = await create_and_index_text_snippet(
        db, user_id=user.user_id, agent_id=payload.agent_id, title=payload.title, snippet_text=payload.text
    )
    return SourceIndexResponse(source=source, job=job)


@router.get("/sources", response_model=TextSnippetsListResponse)
async def list_snippet_sources_route(
    agent_id: UUID = Query(...),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TextSnippetsListResponse:
    sources = await list_text_snippet_sources_for_agent(db, user.user_id, agent_id)
    return TextSnippetsListResponse(sources=sources)


@router.get("/sources/{source_id}", response_model=TextSnippetDetailDTO)
async def snippet_detail_route(
    source_id: UUID,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TextSnippetDetailDTO:
    return await get_text_snippet_detail(db, user.user_id, source_id)


@router.patch("/sources/{source_id}", response_model=SourceIndexResponse)
async def update_snippet_route(
    source_id: UUID,
    payload: TextSnippetUpdateRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SourceIndexResponse:
    source, job = await update_text_snippet_source(
        db, user.user_id, source_id, title=payload.title, snippet_text=payload.text
    )
    return SourceIndexResponse(source=source, job=job)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_snippet_route(
    source_id: UUID,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await delete_text_snippet_source(db, user.user_id, source_id)
    return Response(status_code=204)
