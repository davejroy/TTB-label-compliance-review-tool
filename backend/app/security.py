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
# FastAPI 0.162 Swagger UI / ReDoc load scripts and styles from jsDelivr and
# the FastAPI favicon. default-src stays none; only those docs assets are
# allowed. API JSON routes keep API_CSP.
DOCS_CSP = (
    "default-src 'none'; "
    "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.jsdelivr.net; "
    "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; "
    "connect-src 'self'; "
    "worker-src 'self' blob:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)


def csp_for_path(path: str) -> str:
    """Return the CSP for this request path.

    ``/docs``, ``/redoc``, and ``/openapi.json`` need the docs UI allowlist.
    Every other route, including review JSON, keeps the locked-down API CSP.
    """
    raw = (path or "/").split("?", 1)[0]
    if raw == "/openapi.json" or raw.startswith("/docs") or raw.startswith("/redoc"):
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
                    headers.setdefault(
                        "Content-Security-Policy",
                        csp_for_path(scope.get("path", "")),
                    )
                    if request_is_https(scope):
                        headers.setdefault("Strict-Transport-Security", HSTS_VALUE)
                except Exception:  # noqa: BLE001
                    _log.exception(
                        "Security headers middleware failed; continuing fail-open"
                    )
            await send(message)

        await self.app(scope, receive, send_wrapper)
