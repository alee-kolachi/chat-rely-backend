"""CORS for embeddable widget API — any storefront origin, no credentials."""

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class PublicWidgetCORSMiddleware(BaseHTTPMiddleware):
    """Reflect ``Origin`` for embeddable widget + public chat SSE routes."""

    PREFIXES = ("/api/v1/public/widget", "/api/chat/public")

    def _matches(self, path: str) -> bool:
        return any(path.startswith(p) for p in self.PREFIXES)

    def _cors_headers(self, request: Request) -> dict[str, str]:
        origin = (request.headers.get("origin") or "").strip()
        allow = origin if origin else "*"
        headers: dict[str, str] = {
            "Access-Control-Allow-Origin": allow,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-ChatRely-Agent-Key",
            "Access-Control-Max-Age": "86400",
        }
        if origin:
            headers["Vary"] = "Origin"
        return headers

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        path = request.url.path
        if not self._matches(path):
            return await call_next(request)

        if request.method == "OPTIONS":
            return Response(status_code=204, headers=self._cors_headers(request))

        response = await call_next(request)
        for k, v in self._cors_headers(request).items():
            response.headers[k] = v
        return response
