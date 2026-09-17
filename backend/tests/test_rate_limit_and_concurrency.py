"""Tests for rate limiting, concurrency cap, daily spend guard, and fail-open security.

Verifies:
1. /api/health and /api/demo-info are completely unlimited and fail-open.
2. Expensive review endpoints return HTTP 429 Too Many Requests (NOT 401/403) when rate limit exceeded.
3. Process-wide asyncio.Semaphore concurrency cap enforces limit on Claude calls and returns friendly busy message on timeout.
4. DailySpendGuard tracks requests and emits warning logs when threshold is crossed without blocking.
5. Pillow Image.MAX_IMAGE_PIXELS is explicitly set.
6. Streaming batch endpoint masks raw exception strings.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.claude_client import extract_label_fields
from app.limiter import (
    daily_spend_guard,
    get_client_ip,
    limiter,
)
from app.main import (
    _claude_semaphore,
    app,
    get_claude_semaphore,
    set_claude_semaphore_limit,
)
from app.models import ExtractedLabelData

VALID_APPLICATION = json.dumps({
    "beverage_type": "distilled_spirits",
    "brand_name": "Old Tom Distillery",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
})


def _sample_png(width: int = 150, height: int = 150) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (100, 150, 200)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def reset_limiter():
    """Reset limiter storage and daily spend guard before each test."""
    limiter.reset()
    daily_spend_guard.reset_for_test()
    set_claude_semaphore_limit(5)
    yield
    limiter.reset()
    daily_spend_guard.reset_for_test()
    set_claude_semaphore_limit(5)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_pillow_max_image_pixels_set():
    """Verify Image.MAX_IMAGE_PIXELS is set to 64,000,000 to prevent decompression bombs."""
    assert Image.MAX_IMAGE_PIXELS == 64_000_000


def test_health_and_demo_info_unlimited(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Verify /api/health and /api/demo-info are never rate limited and return 200."""
    monkeypatch.setenv("RATE_LIMIT_REVIEW", "1/minute")
    for _ in range(10):
        resp_health = client.get("/api/health")
        assert resp_health.status_code == 200
        assert resp_health.json() == {"status": "ok"}

        resp_info = client.get("/api/demo-info")
        assert resp_info.status_code == 200
        assert "auth_enabled" in resp_info.json()


def test_client_ip_prefers_x_forwarded_for():
    """Verify get_client_ip extracts first hop from X-Forwarded-For."""
    class FakeRequest:
        headers = {"x-forwarded-for": "203.0.113.195, 70.41.3.18, 150.172.238.178"}
        client = None

    ip = get_client_ip(FakeRequest())
    assert ip == "203.0.113.195"


def test_review_endpoint_rate_limiting_returns_429(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Verify /api/review returns HTTP 429 (not 401/403) with clear retry message when limit exceeded."""
    monkeypatch.setenv("RATE_LIMIT_REVIEW", "2/minute")
    fake_extracted = ExtractedLabelData(
        brand_name="Old Tom Distillery",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General, women should not drink alcoholic beverages during pregnancy.",
    )

    with patch("app.main.extract_label_fields", return_value=fake_extracted):
        files = [("files", ("label.png", _sample_png(), "image/png"))]
        data = {"application": VALID_APPLICATION}

        # Request 1: OK
        r1 = client.post("/api/review", files=files, data=data)
        assert r1.status_code == 200

        # Request 2: OK
        r2 = client.post("/api/review", files=files, data=data)
        assert r2.status_code == 200

        # Request 3: Rate limit exceeded -> HTTP 429
        r3 = client.post("/api/review", files=files, data=data)
        assert r3.status_code == 429
        body = r3.json()
        assert "Rate limit exceeded" in body["detail"]
        assert body["error"] == "too_many_requests"
        assert r3.headers.get("retry-after") is not None


def test_label_check_batch_rate_limiting_returns_429(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Verify /api/label-check/batch is rate limited and returns 429."""
    monkeypatch.setenv("RATE_LIMIT_REVIEW", "1/minute")
    fake_extracted = ExtractedLabelData(
        brand_name="Old Tom Distillery",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General...",
    )

    with patch("app.main.extract_label_fields", return_value=fake_extracted):
        files = [("files", ("label.png", _sample_png(), "image/png"))]
        data = {"image_counts": "[1]", "confirmed_beverage_type": "distilled_spirits"}

        # Request 1: OK
        r1 = client.post("/api/label-check/batch", files=files, data=data)
        assert r1.status_code == 200

        # Request 2: Exceeded -> 429
        r2 = client.post("/api/label-check/batch", files=files, data=data)
        assert r2.status_code == 429


@pytest.mark.asyncio
async def test_concurrency_semaphore_limits_claude_calls():
    """Verify process-wide asyncio.Semaphore wraps Claude calls and enforces concurrency limit."""
    sem = get_claude_semaphore()
    assert sem._value == 5

    # Acquire all permits to simulate saturation
    set_claude_semaphore_limit(1)
    acquired_sem = get_claude_semaphore()
    await acquired_sem.acquire()

    from app.main import _extract_fields_with_retry
    with patch("app.main._CLAUDE_SEMAPHORE_TIMEOUT_S", 0.05):
        extracted, error = await _extract_fields_with_retry([(_sample_png(), "test.png")])
        assert extracted is None
        assert error is not None
        assert "high volume of label requests" in error or "busy" in error.lower()

    acquired_sem.release()


def test_daily_spend_guard_records_and_logs_warning(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch):
    """Verify DailySpendGuard tracks requests and emits loud WARNING when configured daily cap is crossed."""
    monkeypatch.setenv("MAX_REVIEW_REQUESTS_PER_DAY", "3")
    daily_spend_guard.reset_for_test()

    with caplog.at_level(logging.WARNING):
        cnt, over = daily_spend_guard.record_request(2)
        assert cnt == 2
        assert over is False

        cnt, over = daily_spend_guard.record_request(2)
        assert cnt == 4
        assert over is True

    assert any("DAILY REVIEW REQUEST THRESHOLD EXCEEDED" in r.message for r in caplog.records)


def test_streaming_batch_masks_raw_exceptions(client: TestClient):
    """Verify streaming batch path does not leak raw internal exception details to client."""
    with patch("app.main.extract_label_fields", side_effect=Exception("Internal database or key error /var/secret")):
        files = [("files", ("label.png", _sample_png(), "image/png"))]
        data = {"image_counts": "[1]", "applications": json.dumps([{
            "beverage_type": "distilled_spirits",
            "brand_name": "Test",
            "class_type": "Bourbon",
            "alcohol_content": "40% ABV",
            "net_contents": "750 mL"
        }])}

        resp = client.post("/api/review/batch/stream", files=files, data=data)
        assert resp.status_code == 200
        lines = [json.loads(l) for l in resp.text.strip().split("\n") if l.strip()]
        result_line = lines[0]
        assert result_line["index"] == 0
        err = result_line["result"]["error"]
        assert "secret" not in err
        assert "database" not in err
        assert "error occurred while reviewing this label" in err.lower()
