from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user, get_db
from app.core.errors import AppError
from app.domains.knowledge.schemas import FileSourcesListResponse, FileUploadResponse
from app.domains.knowledge.service import (
    create_failed_uploaded_file_source,
    create_and_index_uploaded_file,
    delete_file_source,
    list_file_sources_for_agent,
)

router = APIRouter(prefix="/knowledge/files", tags=["knowledge-files"])


@router.post("/upload", response_model=FileUploadResponse)
async def upload_files_route(
    agent_id: UUID = Form(...),
    files: list[UploadFile] = File(...),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileUploadResponse:
    results = []
    for f in files:
        payload = await f.read()
        try:
            out = await create_and_index_uploaded_file(
                db,
                user_id=user.user_id,
                agent_id=agent_id,
                filename=f.filename or "upload",
                content_type=f.content_type,
                payload=payload,
            )
        except AppError as exc:
            out = await create_failed_uploaded_file_source(
                db,
                user_id=user.user_id,
                agent_id=agent_id,
                filename=f.filename or "upload",
                content_type=f.content_type,
                uploaded_bytes=len(payload),
                error_message=exc.message,
            )
        results.append(out)
    return FileUploadResponse(results=results)


@router.get("/sources", response_model=FileSourcesListResponse)
async def file_sources_route(
    agent_id: UUID = Query(...),
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileSourcesListResponse:
    sources = await list_file_sources_for_agent(db, user.user_id, agent_id)
    return FileSourcesListResponse(sources=sources)


@router.delete("/sources/{source_id}", status_code=204)
async def file_source_delete_route(
    source_id: UUID,
    user: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await delete_file_source(db, user.user_id, source_id)
    return Response(status_code=204)
