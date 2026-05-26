from dataclasses import asdict, dataclass
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request


@dataclass
class ErrorEnvelope:
    code: str
    message: str
    details: dict[str, Any] | None
    request_id: str | None


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class AuthError(AppError):
    def __init__(self, message: str = "Unauthorized", details: dict[str, Any] | None = None):
        super().__init__("auth.unauthorized", message, status_code=401, details=details)


class RateLimitError(AppError):
    def __init__(
        self,
        message: str = "Too many requests. Please try again shortly.",
        *,
        retry_after_seconds: int = 60,
        details: dict[str, Any] | None = None,
    ):
        merged = dict(details or {})
        merged.setdefault("retry_after_seconds", retry_after_seconds)
        super().__init__(
            "rate_limit.exceeded",
            message,
            status_code=429,
            details=merged,
        )
        self.retry_after_seconds = retry_after_seconds


def error_response(
    *,
    code: str,
    message: str,
    status_code: int,
    request_id: str | None,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(code=code, message=message, details=details, request_id=request_id)
    return JSONResponse(status_code=status_code, content={"error": asdict(envelope)}, headers=headers or {})


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    extra_headers: dict[str, str] | None = None
    if isinstance(exc, RateLimitError):
        extra_headers = {"Retry-After": str(max(1, exc.retry_after_seconds))}
    return error_response(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        details=exc.details,
        request_id=getattr(request.state, "request_id", None),
        headers=extra_headers,
    )


async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
    return error_response(
        code="http.error",
        message="Request failed",
        status_code=exc.status_code,
        details=detail,
        request_id=getattr(request.state, "request_id", None),
    )


async def unhandled_error_handler(request: Request, _: Exception) -> JSONResponse:
    return error_response(
        code="internal.error",
        message="Unexpected server error",
        status_code=500,
        details=None,
        request_id=getattr(request.state, "request_id", None),
    )

