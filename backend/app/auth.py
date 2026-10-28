"""Lightweight demo authentication for the TTB Label Compliance Review Tool.

This module provides a *demonstration-grade* access gate so the deployed
prototype is not wide open on the public internet.

Design goals
------------
- **Fail open when unconfigured.** If DEMO_ACCESS_TOKEN is not set in the
  environment, the dependency allows every request. This preserves existing
  local-dev and test behavior and guarantees the app cannot lock itself out
  if an operator forgets to configure the secret.
- **No secrets in source.** The real token lives only in the deployment
  environment. Nothing sensitive is committed.
- **Constant-time comparison** to avoid trivial timing side channels.
- **Failed-auth lockout** (F10): count 401s per client IP (using the same
  TRUSTED_PROXY_HOPS derivation as rate limits). After the configured
  threshold the next attempts return HTTP 429. Counter/store errors fail
  open. ``/api/health`` never uses this dependency.

IMPORTANT: This is a shared-token demo gate, NOT real user authentication or
authorization. It must not be relied on to protect real COLA/PII data. See
docs/TECHNICAL_ARCHITECTURE.md (Authentication and Authorization) for the
production gap this does and does not close.
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request, status

from .limiter import get_client_ip, log_cap_event

_log = logging.getLogger(__name__)

# Environment variable names (documented in backend/.env.example).
_TOKEN_ENV = "DEMO_ACCESS_TOKEN"
_USERNAME_ENV = "DEMO_USERNAME"

# Default username kept for operators; /api/demo-info no longer returns it.
_DEFAULT_USERNAME = "ttb-demo"

AUTH_LOCKOUT_FAILURES_ENV = "AUTH_LOCKOUT_FAILURES"
AUTH_LOCKOUT_WINDOW_SECONDS_ENV = "AUTH_LOCKOUT_WINDOW_SECONDS"
DEFAULT_AUTH_LOCKOUT_FAILURES = 10
DEFAULT_AUTH_LOCKOUT_WINDOW_SECONDS = 600

AUTH_REQUIRED_MESSAGE = "Invalid or missing access token."
AUTH_LOCKOUT_MESSAGE = (
    "Too many failed sign-in attempts. Please wait a few minutes and try again."
)


def _configured_token() -> str:
    """Return the configured demo token, or "" if auth is disabled."""
    return os.environ.get(_TOKEN_ENV, "").strip()


def demo_username() -> str:
    """Return the optional demo username (not exposed by /api/demo-info)."""
    return os.environ.get(_USERNAME_ENV, "").strip() or _DEFAULT_USERNAME


def auth_enabled() -> bool:
    """True when a demo token is configured (i.e. the gate is active)."""
    return bool(_configured_token())


def _extract_bearer(authorization: str | None) -> str | None:
    """Pull the raw token out of an 'Authorization: Bearer <token>' header."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(str(raw).strip() or default)
    except (TypeError, ValueError):
        _log.warning("Invalid %s=%r; using default %s (fail-open)", name, raw, default)
        return default
    if value < 1:
        _log.warning("Invalid %s=%s; using default %s (fail-open)", name, value, default)
        return default
    return value


class AuthFailureGuard:
    """In-memory failed-auth counter keyed by client IP.

    Single-instance only. Store errors never lock a client out (fail-open).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _window(self) -> float:
        return float(_env_int(AUTH_LOCKOUT_WINDOW_SECONDS_ENV, DEFAULT_AUTH_LOCKOUT_WINDOW_SECONDS))

    def _max_failures(self) -> int:
        return _env_int(AUTH_LOCKOUT_FAILURES_ENV, DEFAULT_AUTH_LOCKOUT_FAILURES)

    def _prune_locked(self, ip: str, now: float) -> None:
        window = self._window()
        times = self._failures.get(ip)
        if not times:
            return
        while times and now - times[0] >= window:
            times.popleft()
        if not times:
            self._failures.pop(ip, None)

    def is_locked_out(self, ip: str) -> bool:
        try:
            now = time.monotonic()
            with self._lock:
                self._prune_locked(ip, now)
                times = self._failures.get(ip)
                if not times:
                    return False
                return len(times) >= self._max_failures()
        except Exception:  # noqa: BLE001
            _log.exception("Auth lockout check failed; allowing request (fail-open)")
            return False

    def retry_after_seconds(self, ip: str) -> int:
        try:
            now = time.monotonic()
            window = self._window()
            with self._lock:
                self._prune_locked(ip, now)
                times = self._failures.get(ip)
                if not times:
                    return int(window)
                oldest = times[0]
                return max(1, int(window - (now - oldest)))
        except Exception:  # noqa: BLE001
            _log.exception("Auth lockout retry-after failed; using window default")
            return DEFAULT_AUTH_LOCKOUT_WINDOW_SECONDS

    def record_failure(self, ip: str) -> bool:
        """Record a 401. Returns True when the IP is now locked out."""
        try:
            now = time.monotonic()
            with self._lock:
                self._failures[ip].append(now)
                self._prune_locked(ip, now)
                locked = len(self._failures.get(ip, ())) >= self._max_failures()
            if locked:
                log_cap_event(
                    "cap.auth_lockout",
                    client_ip=ip,
                    failures=self._max_failures(),
                    window_seconds=int(self._window()),
                )
            return locked
        except Exception:  # noqa: BLE001
            _log.exception("Auth failure counter failed; continuing fail-open")
            return False

    def record_success(self, ip: str) -> None:
        try:
            with self._lock:
                self._failures.pop(ip, None)
        except Exception:  # noqa: BLE001
            _log.exception("Auth success reset failed; continuing fail-open")

    def reset_for_test(self) -> None:
        with self._lock:
            self._failures.clear()


auth_failure_guard = AuthFailureGuard()


async def require_demo_access(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """FastAPI dependency enforcing the demo access token and auth lockout.

    - When DEMO_ACCESS_TOKEN is unset, this is a no-op (fail open).
    - A locked-out client IP receives HTTP 429 (never applied to /api/health).
    - Otherwise the request must carry ``Authorization: Bearer <token>`` whose
      value matches DEMO_ACCESS_TOKEN, else 401 is raised.
    """
    expected = _configured_token()
    if not expected:
        return

    try:
        client_ip = get_client_ip(request)
    except Exception:  # noqa: BLE001
        _log.exception("Auth client IP lookup failed; using loopback (fail-open)")
        client_ip = "127.0.0.1"

    try:
        if auth_failure_guard.is_locked_out(client_ip):
            retry_after = auth_failure_guard.retry_after_seconds(client_ip)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=AUTH_LOCKOUT_MESSAGE,
                headers={"Retry-After": str(retry_after)},
            )
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        _log.exception("Auth lockout enforcement failed; allowing request (fail-open)")

    presented = _extract_bearer(authorization)
    if presented is None or not hmac.compare_digest(presented, expected):
        auth_failure_guard.record_failure(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=AUTH_REQUIRED_MESSAGE,
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth_failure_guard.record_success(client_ip)
