"""Rate limiting and abuse prevention helpers for the TTB Label Review API.

Key principles:
1. Fail-open access: Review endpoints are rate-limited per client IP to prevent abuse,
   returning HTTP 429 (Too Many Requests) with retry advice, NOT 401/403.
2. Unrestricted health and metadata: /api/health and /api/demo-info remain strictly unlimited.
3. IP resolution: Prefer the right-most X-Forwarded-For hop added by the trusted
   proxy (Render adds one hop). Clients can spoof the left-most entries.
4. Soft spend/abuse guardrails: Per-IP and optional global daily caps return HTTP 429
   with the RATE_LIMITED contract. If the counter or store errors, the request proceeds.
"""

from __future__ import annotations

import contextvars
import datetime
import json
import logging
import os
import threading

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from .errors import (
    RATE_LIMITED,
    REQUEST_ID_HEADER,
    current_request_id,
    error_payload,
)

_log = logging.getLogger(__name__)

# Environment variable configuration with safe defaults
RATE_LIMIT_ENV = "RATE_LIMIT_REVIEW"
DEFAULT_RATE_LIMIT = "20/minute"

TRUSTED_PROXY_HOPS_ENV = "TRUSTED_PROXY_HOPS"
DEFAULT_TRUSTED_PROXY_HOPS = 1

DAILY_REQUEST_SOFT_CAP_ENV = "DAILY_REQUEST_SOFT_CAP"
DEFAULT_DAILY_REQUEST_SOFT_CAP = 500

MAX_DAILY_REQUESTS_ENV = "MAX_REVIEW_REQUESTS_PER_DAY"
# Generous process-wide ceiling; 0 disables the global cap only.
DEFAULT_MAX_DAILY_REQUESTS = 10_000

_client_ip_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ttb_client_ip", default=""
)


def set_client_ip(client_ip: str) -> contextvars.Token[str]:
    return _client_ip_var.set(client_ip)


def current_client_ip() -> str:
    return _client_ip_var.get() or "127.0.0.1"


def get_trusted_proxy_hops() -> int:
    """Number of XFF hops appended by trusted infrastructure (Render = 1)."""
    raw = os.environ.get(TRUSTED_PROXY_HOPS_ENV, str(DEFAULT_TRUSTED_PROXY_HOPS))
    try:
        hops = int(str(raw).strip() or DEFAULT_TRUSTED_PROXY_HOPS)
    except (TypeError, ValueError):
        _log.warning(
            "Invalid %s=%r; using default %d (fail-open)",
            TRUSTED_PROXY_HOPS_ENV,
            raw,
            DEFAULT_TRUSTED_PROXY_HOPS,
        )
        return DEFAULT_TRUSTED_PROXY_HOPS
    if hops < 0:
        _log.warning(
            "Negative %s=%d; using default %d (fail-open)",
            TRUSTED_PROXY_HOPS_ENV,
            hops,
            DEFAULT_TRUSTED_PROXY_HOPS,
        )
        return DEFAULT_TRUSTED_PROXY_HOPS
    return hops


def _ip_from_xff(forwarded_for: str, hops: int) -> str | None:
    """Return the client IP as observed by the outermost trusted proxy.

    X-Forwarded-For is ``client, proxy1, ..., trusted-hop``. The right-most
    ``hops`` entries are added by trusted proxies (Render adds one). The
    client IP is therefore ``parts[-hops]``. Left-most entries are ignored
    because any client can spoof them.
    """
    parts = [item.strip() for item in forwarded_for.split(",") if item.strip()]
    if not parts or hops <= 0:
        return None
    index = max(0, len(parts) - hops)
    return parts[index] or None


def get_client_ip(request: Request | None = None, *args, **kwargs) -> str:
    """Extract client IP using the trusted (right-most) XFF hop.

    Never raises: header or peer lookup failures fall back to
    ``request.client.host`` and then ``127.0.0.1``.
    """
    try:
        if request is not None:
            try:
                forwarded_for = request.headers.get("x-forwarded-for")
            except Exception:  # noqa: BLE001
                _log.exception("Reading X-Forwarded-For failed; falling back")
                forwarded_for = None
            if forwarded_for:
                try:
                    hops = get_trusted_proxy_hops()
                    client_ip = _ip_from_xff(forwarded_for, hops)
                    if client_ip:
                        return client_ip
                except Exception:  # noqa: BLE001
                    _log.exception("X-Forwarded-For parse failed; falling back")
            try:
                if request.client and request.client.host:
                    return request.client.host
            except Exception:  # noqa: BLE001
                _log.exception("request.client.host lookup failed; using loopback")
    except Exception:  # noqa: BLE001
        _log.exception("get_client_ip failed; using loopback (fail-open)")
    return "127.0.0.1"


def get_review_rate_limit() -> str:
    """Return the configured rate limit string for expensive review endpoints."""
    return os.environ.get(RATE_LIMIT_ENV, DEFAULT_RATE_LIMIT).strip() or DEFAULT_RATE_LIMIT


def utc_today() -> datetime.date:
    """Calendar day used by daily counters and Retry-After (always UTC)."""
    return datetime.datetime.now(datetime.timezone.utc).date()


def daily_cap_retry_after_seconds() -> int:
    """Seconds until the next UTC midnight (when daily counters reset)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    tomorrow = utc_today() + datetime.timedelta(days=1)
    reset_at = datetime.datetime(
        tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=datetime.timezone.utc
    )
    return max(1, int((reset_at - now).total_seconds()))


def log_cap_event(event: str, **fields: object) -> None:
    """Emit a structured warning when a spend/abuse cap trips.

    Fail-open: a logging problem must not change request handling.
    """
    try:
        payload = {
            "event": event,
            "request_id": current_request_id(),
        }
        payload.update(fields)
        _log.warning("%s: %s", event, json.dumps(payload, default=str))
    except Exception:  # noqa: BLE001
        _log.exception("Failed to emit cap event %s", event)


def rate_limited_response(
    request: Request,
    message: str,
    retry_after: int,
    *,
    event: str = "cap.per_ip",
) -> JSONResponse:
    """HTTP 429 body matching the PR E error contract."""
    client_ip = get_client_ip(request)
    request_id = current_request_id()
    log_cap_event(
        event,
        client_ip=client_ip,
        path=str(request.url.path),
        message=message,
        retry_after=retry_after,
    )
    return JSONResponse(
        status_code=429,
        content=error_payload(
            RATE_LIMITED,
            message,
            request_id=request_id,
            extra={
                "error": "too_many_requests",
                "retry_after": retry_after,
            },
        ),
        headers={
            "Retry-After": str(retry_after),
            REQUEST_ID_HEADER: request_id,
        },
    )


limiter = Limiter(
    key_func=get_client_ip,
    default_limits=[],
    headers_enabled=False,
    strategy="moving-window",
    swallow_errors=True,
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Custom 429 response handler returning a friendly, actionable retry message.

    Returns HTTP 429 Too Many Requests (never 401 Unauthorized or 403 Forbidden).
    """
    retry_after = getattr(exc, "retry_after", 60) or 60
    message = (
        "Too many requests. Please wait a moment before submitting additional "
        "label reviews."
    )
    return rate_limited_response(request, message, int(retry_after), event="cap.per_ip")


class DailyCapExceeded(Exception):
    """Raised when a configured daily soft cap is already exhausted."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        self.detail = "Daily review limit reached. Please try again tomorrow."
        super().__init__(self.detail)


class DailySpendGuard:
    """Process-wide soft guardrail for review requests per day.

    Tracks daily request counts in-memory for single-instance deployments.
    Per-IP and global ceilings both return HTTP 429 when already exceeded.
    Store/parse errors never block a request (fail-open).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_date = utc_today()
        self._count = 0
        self._per_ip: dict[str, int] = {}
        self._warned_threshold = False

    def _env_int(self, name: str, default: int) -> int:
        raw = os.environ.get(name, str(default))
        try:
            return int(str(raw).strip() or default)
        except (TypeError, ValueError):
            _log.warning("Invalid %s=%r; using default %s (fail-open)", name, raw, default)
            return default

    def _get_max_daily(self) -> int:
        return self._env_int(MAX_DAILY_REQUESTS_ENV, DEFAULT_MAX_DAILY_REQUESTS)

    def _get_per_ip_cap(self) -> int:
        return self._env_int(DAILY_REQUEST_SOFT_CAP_ENV, DEFAULT_DAILY_REQUEST_SOFT_CAP)

    def _roll_date_locked(self) -> None:
        today = utc_today()
        if today != self._current_date:
            self._current_date = today
            self._count = 0
            self._per_ip = {}
            self._warned_threshold = False

    def is_over_cap(self, client_ip: str | None = None, additional: int = 1) -> bool:
        """True when ``additional`` more reviews would exceed a configured cap.

        Returns False on any internal error so callers can fail open.
        """
        try:
            ip = client_ip or current_client_ip()
            extra = max(1, int(additional))
            per_ip_cap = self._get_per_ip_cap()
            global_cap = self._get_max_daily()
            with self._lock:
                self._roll_date_locked()
                if per_ip_cap > 0 and self._per_ip.get(ip, 0) + extra > per_ip_cap:
                    return True
                if global_cap > 0 and self._count + extra > global_cap:
                    return True
            return False
        except Exception:  # noqa: BLE001
            _log.exception("Daily cap check failed; allowing request (fail-open)")
            return False

    def reserve(self, count: int = 1, client_ip: str | None = None) -> int:
        """Atomically reserve *count* reviews before a model call.

        Raises ``DailyCapExceeded`` when the per-IP soft cap or the global
        daily cap would be exceeded. Store/parse errors are logged and the
        reservation is skipped (fail-open) so a broken counter never blocks
        a legitimate review.
        """
        try:
            ip = client_ip or current_client_ip()
            extra = max(1, int(count))
            per_ip_cap = self._get_per_ip_cap()
            global_cap = self._get_max_daily()
            with self._lock:
                self._roll_date_locked()
                next_ip = self._per_ip.get(ip, 0) + extra
                next_global = self._count + extra
                if per_ip_cap > 0 and next_ip > per_ip_cap:
                    log_cap_event(
                        "cap.daily_soft",
                        client_ip=ip,
                        attempted=next_ip,
                        limit=per_ip_cap,
                    )
                    raise DailyCapExceeded(daily_cap_retry_after_seconds())
                if global_cap > 0 and next_global > global_cap:
                    log_cap_event(
                        "cap.global_daily",
                        client_ip=ip,
                        attempted=next_global,
                        limit=global_cap,
                    )
                    raise DailyCapExceeded(daily_cap_retry_after_seconds())
                self._count = next_global
                self._per_ip[ip] = next_ip
                return self._count
        except DailyCapExceeded:
            raise
        except Exception:  # noqa: BLE001
            _log.exception("Daily cap reserve failed; allowing request (fail-open)")
            return 0

    def record_request(self, count: int = 1, client_ip: str | None = None) -> tuple[int, bool]:
        """Record review request(s) after validation and the quality pre-gate.

        Returns (current_daily_count, is_over_threshold). Errors are logged and
        reported as (0, False) so a broken counter never blocks a review.
        """
        try:
            ip = client_ip or current_client_ip()
            max_daily = self._get_max_daily()
            per_ip_cap = self._get_per_ip_cap()

            with self._lock:
                self._roll_date_locked()
                self._count += count
                self._per_ip[ip] = self._per_ip.get(ip, 0) + count
                current = self._count
                per_ip_count = self._per_ip[ip]
                is_over = (
                    (max_daily > 0 and current > max_daily)
                    or (per_ip_cap > 0 and per_ip_count > per_ip_cap)
                )

                if is_over and not self._warned_threshold:
                    self._warned_threshold = True
                    event = (
                        "cap.daily_soft"
                        if per_ip_cap > 0 and per_ip_count > per_ip_cap
                        else "cap.global_daily"
                    )
                    log_cap_event(
                        event,
                        client_ip=ip,
                        global_count=current,
                        global_limit=max_daily,
                        ip_count=per_ip_count,
                        ip_limit=per_ip_cap,
                    )
                    _log.warning(
                        "DAILY REVIEW REQUEST THRESHOLD EXCEEDED: %d global / %d for %s "
                        "(MAX_REVIEW_REQUESTS_PER_DAY=%d, DAILY_REQUEST_SOFT_CAP=%d).",
                        current,
                        per_ip_count,
                        ip,
                        max_daily,
                        per_ip_cap,
                    )
                elif is_over:
                    _log.warning(
                        "Daily review request threshold exceeded (global=%d/%d, ip=%s %d/%d).",
                        current,
                        max_daily,
                        ip,
                        per_ip_count,
                        per_ip_cap,
                    )

                return current, is_over
        except Exception:  # noqa: BLE001
            _log.exception("Daily cap counter failed; allowing request (fail-open)")
            return 0, False

    def get_count(self) -> int:
        try:
            with self._lock:
                if utc_today() != self._current_date:
                    return 0
                return self._count
        except Exception:  # noqa: BLE001
            _log.exception("Daily cap get_count failed; returning 0 (fail-open)")
            return 0

    def get_ip_count(self, client_ip: str) -> int:
        try:
            with self._lock:
                if utc_today() != self._current_date:
                    return 0
                return self._per_ip.get(client_ip, 0)
        except Exception:  # noqa: BLE001
            _log.exception("Daily cap get_ip_count failed; returning 0 (fail-open)")
            return 0

    def reset_for_test(self) -> None:
        with self._lock:
            self._current_date = utc_today()
            self._count = 0
            self._per_ip = {}
            self._warned_threshold = False


daily_spend_guard = DailySpendGuard()


def raise_if_daily_capped(request: Request, additional: int = 1) -> None:
    """HTTP 429 when this client cannot take ``additional`` more reviews today.

    Call this from application code after the label count is known. Do not
    register this function as a FastAPI ``Depends`` — extra parameters would
    be bound as query args. Use ``require_daily_cap`` for route dependencies.

    Store/parse failures are logged and the request proceeds.
    """
    try:
        extra = max(1, int(additional))
    except (TypeError, ValueError):
        extra = 1
    try:
        ip = get_client_ip(request)
        if daily_spend_guard.is_over_cap(ip, additional=extra):
            raise DailyCapExceeded(daily_cap_retry_after_seconds())
    except DailyCapExceeded:
        raise
    except Exception:  # noqa: BLE001
        _log.exception("Daily cap enforcement failed; allowing request (fail-open)")


def require_daily_cap(request: Request) -> None:
    """Route dependency: reject when this client is already at the daily cap."""
    raise_if_daily_capped(request, additional=1)


def daily_cap_exceeded_handler(request: Request, exc: DailyCapExceeded) -> Response:
    return rate_limited_response(
        request,
        str(exc.detail),
        int(getattr(exc, "retry_after", 0) or daily_cap_retry_after_seconds()),
        event="cap.daily_soft",
    )


def record_validated_review(count: int = 1) -> None:
    """Increment daily counters after validation and pre-gate. Fail-open."""
    try:
        daily_spend_guard.record_request(count)
    except Exception:  # noqa: BLE001
        _log.exception("Daily cap record failed; allowing request (fail-open)")


def reserve_validated_review(count: int = 1) -> None:
    """Atomically reserve daily budget after validation, before the model call.

    Raises ``DailyCapExceeded`` when a cap is already exhausted. Store errors
    are logged and the request proceeds (fail-open).
    """
    try:
        daily_spend_guard.reserve(count)
    except DailyCapExceeded:
        raise
    except Exception:  # noqa: BLE001
        _log.exception("Daily cap reserve failed; allowing request (fail-open)")
