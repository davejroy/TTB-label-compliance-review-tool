"""Shared pytest fixtures for the backend suite."""

from __future__ import annotations

import pytest

from app.auth import auth_failure_guard
from app.limiter import daily_spend_guard, limiter


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """Keep review-route tests isolated from the 20/minute default cap."""
    limiter.reset()
    daily_spend_guard.reset_for_test()
    auth_failure_guard.reset_for_test()
    yield
    limiter.reset()
    daily_spend_guard.reset_for_test()
    auth_failure_guard.reset_for_test()
