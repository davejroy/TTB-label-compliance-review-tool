"""Rate limiting and abuse prevention helpers for the TTB Label Review API.

Key principles:
1. Fail-open access: Review endpoints are rate-limited per client IP to prevent abuse,
   returning HTTP 429 (Too Many Requests) with retry advice, NOT 401/403.
2. Unrestricted health and metadata: /api/health and /api/demo-info remain strictly unlimited.
3. IP resolution: Checks the first hop of X-Forwarded-For if behind a reverse proxy (e.g. Render),
   falling back to request.client.host or "127.0.0.1".
4. Soft spend/abuse guardrails: Tracks daily review requests and logs a loud WARNING if the
   daily threshold is crossed. Multi-instance setups will require Redis later; single-process
   counter is provided for the single Render instance.
"""

from __future__ import annotations

import datetime
import logging
import os
import threading

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

_log = logging.getLogger(__name__)

# Environment variable configuration with safe defaults
RATE_LIMIT_ENV = "RATE_LIMIT_REVIEW"
DEFAULT_RATE_LIMIT = "20/minute"

MAX_DAILY_REQUESTS_ENV = "MAX_REVIEW_REQUESTS_PER_DAY"
# 0 or unset means soft warning on exceed; no hard block
DEFAULT_MAX_DAILY_REQUESTS = 0


def get_client_ip(request: Request | None = None, *args, **kwargs) -> str:
    """Extract client IP address, preferring the first hop of X-Forwarded-For if present.

    FastAPI/Starlette request headers are checked for 'x-forwarded-for'.
    If present, the first IP (the original client) is returned.
    Otherwise, request.client.host is used.
    """
    if request is not None:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            # X-Forwarded-For: client, proxy1, proxy2...
            client_ip = forwarded_for.split(",")[0].strip()
            if client_ip:
                return client_ip
        if request.client and request.client.host:
            return request.client.host
    return "127.0.0.1"


def get_review_rate_limit() -> str:
    """Return the configured rate limit string for expensive review endpoints."""
    return os.environ.get(RATE_LIMIT_ENV, DEFAULT_RATE_LIMIT).strip() or DEFAULT_RATE_LIMIT


limiter = Limiter(
    key_func=get_client_ip,
    default_limits=[],
    headers_enabled=False,
    strategy="moving-window",
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Custom 429 response handler returning a friendly, actionable retry message.

    Returns HTTP 429 Too Many Requests (never 401 Unauthorized or 403 Forbidden).
    """
    client_ip = get_client_ip(request)
    _log.warning(
        "Rate limit exceeded for IP %s on %s: %s",
        client_ip,
        request.url.path,
        exc.detail,
    )
    return JSONResponse(
        status_code=429,
        content={
            "detail": f"Rate limit exceeded: {exc.detail}. Please wait a moment before submitting additional label reviews.",
            "error": "too_many_requests",
            "retry_after": getattr(exc, "retry_after", 60),
        },
        headers={
            "Retry-After": str(getattr(exc, "retry_after", 60)),
        },
    )


class DailySpendGuard:
    """Process-wide soft guardrail for review requests per day.

    Tracks daily request counts in-memory for single-instance deployments (e.g. Render free tier).
    Multi-instance horizontal scaling will require Redis-backed shared state in future iterations.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_date = datetime.date.today()
        self._count = 0
        self._warned_threshold = False

    def _get_max_daily(self) -> int:
        val = os.environ.get(MAX_DAILY_REQUESTS_ENV, str(DEFAULT_MAX_DAILY_REQUESTS)).strip()
        try:
            return int(val)
        except ValueError:
            return 0

    def record_request(self, count: int = 1) -> tuple[int, bool]:
        """Record review request(s).

        Returns (current_daily_count, is_over_threshold).
        """
        today = datetime.date.today()
        max_daily = self._get_max_daily()

        with self._lock:
            if today != self._current_date:
                self._current_date = today
                self._count = 0
                self._warned_threshold = False

            self._count += count
            current = self._count
            is_over = max_daily > 0 and current > max_daily

            if is_over and not self._warned_threshold:
                self._warned_threshold = True
                _log.warning(
                    "⚠️ DAILY REVIEW REQUEST THRESHOLD EXCEEDED: %d requests today (configured MAX_REVIEW_REQUESTS_PER_DAY=%d). "
                    "Notice: Monitoring spend guardrail. App remains operational (fail-open) unless hard cap is configured.",
                    current,
                    max_daily,
                )
            elif is_over:
                _log.warning(
                    "Daily review request threshold exceeded (current count: %d, limit: %d).",
                    current,
                    max_daily,
                )

            return current, is_over

    def get_count(self) -> int:
        with self._lock:
            if datetime.date.today() != self._current_date:
                return 0
            return self._count

    def reset_for_test(self) -> None:
        with self._lock:
            self._current_date = datetime.date.today()
            self._count = 0
            self._warned_threshold = False


daily_spend_guard = DailySpendGuard()
