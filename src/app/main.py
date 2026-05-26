import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import get_api_router
from app.api.routes.chat import router as chat_router
from app.api.routes.chat_public import router as chat_public_router
from app.core.auth_state import set_token_verifier
from app.core.errors import (
    AppError,
    app_error_handler,
    error_response,
    http_error_handler,
    unhandled_error_handler,
)
from app.core.logging import bind_request_context, clear_request_context, setup_logging
from app.core.security import TokenVerifier
from app.core.settings import get_settings, validate_settings
from app.db.engine import get_engine, init_engine
from app.db.session import check_db_ready, init_session_factory
from app.domains.runtime.runtime_cache_warmup import warm_all_runtime_caches
from app.middleware.public_widget_cors import PublicWidgetCORSMiddleware
from app.middleware.rate_limit import RateLimitMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    validate_settings()
    settings = get_settings()
    indexing_worker_task: asyncio.Task[None] | None = None
    setup_logging(
        settings.log_level,
        log_file_enabled=settings.log_file_enabled,
        log_file_path=settings.log_file_path,
        log_file_max_bytes=settings.log_file_max_bytes,
        log_file_backup_count=settings.log_file_backup_count,
        log_pretty_file_enabled=settings.log_pretty_file_enabled,
        log_pretty_file_path=settings.log_pretty_file_path,
        log_pretty_file_max_bytes=settings.log_pretty_file_max_bytes,
        log_pretty_file_backup_count=settings.log_pretty_file_backup_count,
    )
    init_engine(settings)
    init_session_factory()
    await check_db_ready()
    await warm_all_runtime_caches()
    # Always install a real verifier when a Bearer token is present. Dev bypass (see deps.py) only
    # applies to requests *without* Authorization — otherwise every logged-in user would share
    # DEV_AUTH_BYPASS_USER_ID because verify_token was never run.
    verifier = TokenVerifier(settings)
    await verifier.warmup()
    set_token_verifier(verifier)
    if settings.is_development and settings.indexing_worker_embedded_in_dev:
        from app.workers.indexing_worker import run_worker_loop

        indexing_worker_task = asyncio.create_task(run_worker_loop(poll_interval_seconds=2.0))
    yield
    if indexing_worker_task is not None:
        indexing_worker_task.cancel()
        try:
            await indexing_worker_task
        except asyncio.CancelledError:
            pass
    await get_engine().dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    allowed_origins = [str(origin) for origin in settings.allowed_origins]
    if settings.is_development and not allowed_origins:
        allowed_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(PublicWidgetCORSMiddleware)
    app.add_middleware(RateLimitMiddleware)

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next: Any) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        request.state.request_id = request_id
        bind_request_context(request_id=request_id, path=request.url.path, method=request.method)
        start = time.perf_counter()
        logger = structlog.get_logger("request")
        try:
            response = await call_next(request)
        finally:
            latency_ms = int((time.perf_counter() - start) * 1000)
            logger.info("request.completed", status_code=getattr(locals().get("response"), "status_code", 500), latency_ms=latency_ms)
            clear_request_context()

        response.headers["x-request-id"] = request_id
        return response

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return await app_error_handler(request, exc)

    @app.exception_handler(HTTPException)
    async def _handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return await http_error_handler(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_starlette_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return await http_error_handler(request, HTTPException(status_code=exc.status_code, detail=exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            code="request.validation_error",
            message="Invalid request payload",
            status_code=422,
            details={"errors": exc.errors()},
            request_id=getattr(request.state, "request_id", None),
        )

    @app.exception_handler(Exception)
    async def _handle_unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        return await unhandled_error_handler(request, exc)

    app.include_router(get_api_router())
    app.include_router(chat_router, prefix="/api/chat")
    app.include_router(chat_public_router, prefix="/api/chat")
    return app

