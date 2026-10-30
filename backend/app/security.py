"""HTTP security headers and production-runtime helpers.

Applied with a raw ASGI wrapper so streaming (NDJSON / SSE-style) responses
are not buffered. Header failures are logged and swallowed (fail-open).
"""

from __future__ import annotations

import logging
import os

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_log = logging.getLogger(__name__)

PERMISSIONS_POLICY = "camera=(self), microphone=(), geolocation=()"
HSTS_VALUE = "max-age=31536000; includeSubDomains"
API_CSP = "default-src 'none'; frame-ancestors 'none'"
# Interactive OpenAPI docs (ENABLE_API_DOCS=true) need the FastAPI/Swagger CDN.
# Keep this off the JSON API surface.
DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.jsdelivr.net; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "frame-ancestors 'none'"
)
_DOCS_PATH_PREFIXES = ("/docs", "/redoc")


def csp_for_path(path: str) -> str:
    """Return the CSP for *path*. Docs HTML gets a separate, usable policy."""
    normalized = (path or "").rstrip("/") or "/"
    if normalized in _DOCS_PATH_PREFIXES or normalized.startswith(("/docs/", "/redoc/")):
        return DOCS_CSP
    return API_CSP


def is_production_runtime() -> bool:
    """Return True when this process should never emit a CORS wildcard.

    Explicit ENVIRONMENT/APP_ENV=production|prod wins. A Render service whose
    name does not contain ``dev`` is treated as production so promoting the
    same image from the -dev service to the production service yields an
    allowlist even if the dashboard still has ``CORS_ORIGINS=*``.
    """
    env = (
        os.environ.get("ENVIRONMENT")
        or os.environ.get("APP_ENV")
        or ""
    ).strip().lower()
    if env in ("production", "prod"):
        return True
    if env in ("development", "dev", "local", "test", "ci"):
        return False
    service = os.environ.get("RENDER_SERVICE_NAME", "").strip().lower()
    if service:
        return "dev" not in service
    return False


def request_is_https(scope: Scope) -> bool:
    """True when the request was served (or forwarded) over HTTPS."""
    if scope.get("scheme") == "https":
        return True
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }
    return headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"


class SecurityHeadersMiddleware:
    """Attach a small, streaming-safe set of security headers.

    Does not set Content-Length or rewrite Content-Type, so NDJSON streams
    keep flowing. Failures applying headers never reject the request.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                try:
                    headers = MutableHeaders(scope=message)
                    headers.setdefault("X-Content-Type-Options", "nosniff")
                    headers.setdefault(
                        "Referrer-Policy", "strict-origin-when-cross-origin"
                    )
                    headers.setdefault("X-Frame-Options", "DENY")
                    headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
                    headers.setdefault("Content-Security-Policy", csp_for_path(scope.get("path", "")))
                    if request_is_https(scope):
                        headers.setdefault("Strict-Transport-Security", HSTS_VALUE)
                except Exception:  # noqa: BLE001
                    _log.exception(
                        "Security headers middleware failed; continuing fail-open"
                    )
            await send(message)

        await self.app(scope, receive, send_wrapper)
