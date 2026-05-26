from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user, get_db
from app.domains.knowledge.schemas import (
    QAPairCreateRequest,
    QAPairDetailDTO,
    QAPairsListResponse,
    QAPairUpdateRequest,
    SourceIndexResponse,
)
from app.domains.knowledge.service import (
    create_and_index_qa_pair,
    delete_qa_source,
    get_qa_pair_detail,
    list_qa_sources_for_agent,
    update_qa_pair_source,
)

router = APIRouter(prefix="/knowledge/qa", tags=["knowledge-qa"])


@router.post("", response_model=SourceIndexResponse)
async def create_qa_route(
    payload: QAPairCreateRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SourceIndexResponse:
    source, job = await create_and_index_qa_pair(
        db, user_id=user.user_id, agent_id=payload.agent_id, question=payload.question, answer=payload.answer
    )
    return SourceIndexResponse(source=source, job=job)


@router.get("/sources", response_model=QAPairsListResponse)
async def list_qa_sources_route(
    agent_id: UUID = Query(...),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QAPairsListResponse:
    sources = await list_qa_sources_for_agent(db, user.user_id, agent_id)
    return QAPairsListResponse(sources=sources)


@router.get("/sources/{source_id}", response_model=QAPairDetailDTO)
async def qa_detail_route(
    source_id: UUID,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QAPairDetailDTO:
    return await get_qa_pair_detail(db, user.user_id, source_id)


@router.patch("/sources/{source_id}", response_model=SourceIndexResponse)
async def update_qa_route(
    source_id: UUID,
    payload: QAPairUpdateRequest,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SourceIndexResponse:
    source, job = await update_qa_pair_source(
        db, user.user_id, source_id, question=payload.question, answer=payload.answer
    )
    return SourceIndexResponse(source=source, job=job)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_qa_route(
    source_id: UUID,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await delete_qa_source(db, user.user_id, source_id)
    return Response(status_code=204)
