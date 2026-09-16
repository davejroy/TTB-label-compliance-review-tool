"""Lightweight demo authentication for the TTB Label Compliance Review Tool.

This module provides a *demonstration-grade* access gate so the deployed
prototype is not wide open on the public internet, while ensuring TTB
evaluators are never locked out (the expected credentials are surfaced to the
login UI via /api/demo-info).

Design goals
------------
- **Fail open when unconfigured.** If DEMO_ACCESS_TOKEN is not set in the
  environment, the dependency allows every request. This preserves existing
  local-dev and test behavior and guarantees the app cannot lock itself out
  if an operator forgets to configure the secret.
- **No secrets in source.** The real token lives only in the deployment
  environment (Render env var, sync: false). Nothing sensitive is committed.
- **Constant-time comparison** to avoid trivial timing side channels.

IMPORTANT: This is a shared-token demo gate, NOT real user authentication or
authorization. It must not be relied on to protect real COLA/PII data. See
docs/TECHNICAL_ARCHITECTURE.md (Authentication and Authorization) for the
production gap this does and does not close.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException, status

# Environment variable names (documented in backend/.env.example and render.yaml).
_TOKEN_ENV = "DEMO_ACCESS_TOKEN"
_USERNAME_ENV = "DEMO_USERNAME"

# Default username shown to evaluators when DEMO_USERNAME is not set. This is a
# non-secret display value only; the token is what actually grants access.
_DEFAULT_USERNAME = "ttb-demo"


def _configured_token() -> str:
    """Return the configured demo token, or "" if auth is disabled."""
    return os.environ.get(_TOKEN_ENV, "").strip()


def demo_username() -> str:
    """Return the demo username to display in the login UI (non-secret)."""
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


async def require_demo_access(
    authorization: str | None = Header(default=None),
) -> None:
    """FastAPI dependency enforcing the demo access token.

    - When DEMO_ACCESS_TOKEN is unset, this is a no-op (fail open).
    - Otherwise the request must carry 'Authorization: Bearer <token>' whose
      value matches DEMO_ACCESS_TOKEN, else 401 is raised.
    """
    expected = _configured_token()
    if not expected:
        # Auth disabled — allow all. Keeps dev/tests and unconfigured
        # deployments working exactly as before.
        return

    presented = _extract_bearer(authorization)
    if presented is None or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing demo access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
