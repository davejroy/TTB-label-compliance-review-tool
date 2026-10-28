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
    daily_cap_retry_after_seconds,
    daily_spend_guard,
    get_client_ip,
    limiter,
    record_validated_review,
    utc_today,
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


def test_client_ip_uses_rightmost_trusted_xff_hop(monkeypatch: pytest.MonkeyPatch):
    """Trusted hop is the right-most XFF entry (the one Render appends)."""
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")

    class FakeRequest:
        headers = {"x-forwarded-for": "203.0.113.195, 70.41.3.18, 150.172.238.178"}
        client = None

    ip = get_client_ip(FakeRequest())
    assert ip == "150.172.238.178"


def test_client_ip_falls_back_to_peer_host():
    class FakeClient:
        host = "198.51.100.20"

    class FakeRequest:
        headers = {}
        client = FakeClient()

    assert get_client_ip(FakeRequest()) == "198.51.100.20"


def test_client_ip_header_error_fails_open():
    """A broken header store must not raise; fall back to the peer address."""

    class BoomHeaders:
        def get(self, _name: str):
            raise RuntimeError("header store down")

    class FakeClient:
        host = "198.51.100.9"

    class FakeRequest:
        headers = BoomHeaders()
        client = FakeClient()

    assert get_client_ip(FakeRequest()) == "198.51.100.9"


def test_spoofed_leftmost_xff_cannot_bypass_limit(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """A client-supplied left-most XFF entry must not get its own rate-limit bucket."""
    monkeypatch.setenv("RATE_LIMIT_REVIEW", "1/minute")
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    fake_extracted = ExtractedLabelData(
        brand_name="Old Tom Distillery",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General, women should not drink alcoholic beverages during pregnancy.",
        extraction_confidence=0.95,
    )
    files = [("files", ("label.png", _sample_png(), "image/png"))]
    data = {"application": VALID_APPLICATION}

    with patch("app.main.extract_label_fields", return_value=fake_extracted):
        first = client.post(
            "/api/review",
            files=files,
            data=data,
            headers={"X-Forwarded-For": "203.0.113.1, 198.51.100.10"},
        )
        assert first.status_code == 200

        spoofed = client.post(
            "/api/review",
            files=files,
            data=data,
            headers={"X-Forwarded-For": "203.0.113.99, 198.51.100.10"},
        )
        assert spoofed.status_code == 429
        body = spoofed.json()
        assert body["error_code"] == "RATE_LIMITED"
        assert body["error"] == "too_many_requests"
        assert body.get("request_id")
        assert spoofed.headers.get("retry-after")
        assert spoofed.headers.get("x-request-id")

        other_client = client.post(
            "/api/review",
            files=files,
            data=data,
            headers={"X-Forwarded-For": "203.0.113.1, 198.51.100.11"},
        )
        assert other_client.status_code == 200


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
        assert "too many requests" in body["detail"].lower() or "too many requests" in body["message"].lower()
        assert body["error"] == "too_many_requests"
        assert body["error_code"] == "RATE_LIMITED"
        assert body.get("request_id")
        assert r3.headers.get("retry-after") is not None
        assert r3.headers.get("x-request-id")


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
        message = str(error)
        assert "high volume of label requests" in message or "busy" in message.lower()
        assert getattr(error, "code", None) == "RATE_LIMITED"

    acquired_sem.release()


def test_daily_spend_guard_records_and_logs_warning(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch):
    """Verify DailySpendGuard tracks requests and emits loud WARNING when configured daily cap is crossed."""
    monkeypatch.setenv("MAX_REVIEW_REQUESTS_PER_DAY", "3")
    monkeypatch.setenv("DAILY_REQUEST_SOFT_CAP", "0")
    daily_spend_guard.reset_for_test()

    with caplog.at_level(logging.WARNING):
        cnt, over = daily_spend_guard.record_request(2, client_ip="198.51.100.1")
        assert cnt == 2
        assert over is False

        cnt, over = daily_spend_guard.record_request(2, client_ip="198.51.100.1")
        assert cnt == 4
        assert over is True

    assert any("DAILY REVIEW REQUEST THRESHOLD EXCEEDED" in r.message for r in caplog.records)


def test_daily_soft_cap_returns_429_contract(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Per-IP daily cap returns HTTP 429 with RATE_LIMITED, Retry-After, and request_id."""
    monkeypatch.setenv("DAILY_REQUEST_SOFT_CAP", "1")
    monkeypatch.setenv("MAX_REVIEW_REQUESTS_PER_DAY", "0")
    monkeypatch.setenv("RATE_LIMIT_REVIEW", "100/minute")
    daily_spend_guard.reset_for_test()
    fake_extracted = ExtractedLabelData(
        brand_name="Old Tom Distillery",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General, women should not drink alcoholic beverages during pregnancy.",
        extraction_confidence=0.95,
    )
    files = [("files", ("label.png", _sample_png(), "image/png"))]
    data = {"application": VALID_APPLICATION}

    with patch("app.main.extract_label_fields", return_value=fake_extracted):
        first = client.post("/api/review", files=files, data=data)
        assert first.status_code == 200
        blocked = client.post("/api/review", files=files, data=data)

    assert blocked.status_code == 429
    body = blocked.json()
    assert body["error_code"] == "RATE_LIMITED"
    assert body["error"] == "too_many_requests"
    assert "Daily review limit" in body["message"]
    assert body.get("request_id")
    assert blocked.headers.get("retry-after")
    assert int(blocked.headers["retry-after"]) >= 1
    assert blocked.headers.get("x-request-id") == body["request_id"]


def test_daily_cap_store_failure_fails_open(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """A broken daily-cap store must not lock out a valid review."""
    monkeypatch.setenv("DAILY_REQUEST_SOFT_CAP", "1")
    monkeypatch.setenv("RATE_LIMIT_REVIEW", "100/minute")
    fake_extracted = ExtractedLabelData(
        brand_name="Old Tom Distillery",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General, women should not drink alcoholic beverages during pregnancy.",
        extraction_confidence=0.95,
    )
    with (
        patch.object(daily_spend_guard, "is_over_cap", side_effect=RuntimeError("store down")),
        patch.object(daily_spend_guard, "record_request", side_effect=RuntimeError("store down")),
        patch.object(daily_spend_guard, "reserve", side_effect=RuntimeError("store down")),
        patch("app.main.extract_label_fields", return_value=fake_extracted),
    ):
        resp = client.post(
            "/api/review",
            files=[("files", ("label.png", _sample_png(), "image/png"))],
            data={"application": VALID_APPLICATION},
        )
    assert resp.status_code == 200
    assert resp.json()["overall_status"] != "error" or resp.json().get("error_code") != "RATE_LIMITED"


def test_record_validated_review_swallows_counter_errors():
    """record_validated_review must never raise when the counter fails."""
    with patch.object(daily_spend_guard, "record_request", side_effect=RuntimeError("boom")):
        record_validated_review(1)


def test_batch_cannot_bypass_daily_cap(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """A multi-label batch that would exceed the per-IP cap is rejected as 429."""
    monkeypatch.setenv("DAILY_REQUEST_SOFT_CAP", "1")
    monkeypatch.setenv("MAX_REVIEW_REQUESTS_PER_DAY", "0")
    monkeypatch.setenv("RATE_LIMIT_REVIEW", "100/minute")
    daily_spend_guard.reset_for_test()
    fake_extracted = ExtractedLabelData(
        brand_name="Old Tom Distillery",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General, women should not drink alcoholic beverages during pregnancy.",
        extraction_confidence=0.95,
    )
    application = {
        "beverage_type": "distilled_spirits",
        "brand_name": "Test",
        "class_type": "Bourbon",
        "alcohol_content": "40% ABV",
        "net_contents": "750 mL",
    }
    mocked = patch("app.main.extract_label_fields", return_value=fake_extracted)
    with mocked as extract:
        resp = client.post(
            "/api/review/batch",
            files=[
                ("files", ("a.png", _sample_png(), "image/png")),
                ("files", ("b.png", _sample_png(), "image/png")),
            ],
            data={
                "image_counts": "[1,1]",
                "applications": json.dumps([application, application]),
            },
        )
    assert resp.status_code == 429
    body = resp.json()
    assert body["error_code"] == "RATE_LIMITED"
    assert body.get("request_id")
    assert resp.headers.get("retry-after")
    extract.assert_not_called()
    assert daily_spend_guard.get_count() == 0


def test_stream_batch_cannot_bypass_daily_cap(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DAILY_REQUEST_SOFT_CAP", "1")
    monkeypatch.setenv("MAX_REVIEW_REQUESTS_PER_DAY", "0")
    monkeypatch.setenv("RATE_LIMIT_REVIEW", "100/minute")
    daily_spend_guard.reset_for_test()
    application = {
        "beverage_type": "distilled_spirits",
        "brand_name": "Test",
        "class_type": "Bourbon",
        "alcohol_content": "40% ABV",
        "net_contents": "750 mL",
    }
    with patch("app.main.extract_label_fields") as extract:
        resp = client.post(
            "/api/review/batch/stream",
            files=[
                ("files", ("a.png", _sample_png(), "image/png")),
                ("files", ("b.png", _sample_png(), "image/png")),
            ],
            data={
                "image_counts": "[1,1]",
                "applications": json.dumps([application, application]),
            },
        )
    assert resp.status_code == 429
    assert resp.json()["error_code"] == "RATE_LIMITED"
    extract.assert_not_called()


def test_additional_query_param_cannot_bypass_daily_cap(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Depends must not expose `additional` as a client-controlled query param."""
    monkeypatch.setenv("DAILY_REQUEST_SOFT_CAP", "1")
    monkeypatch.setenv("MAX_REVIEW_REQUESTS_PER_DAY", "0")
    monkeypatch.setenv("RATE_LIMIT_REVIEW", "100/minute")
    daily_spend_guard.reset_for_test()
    fake_extracted = ExtractedLabelData(
        brand_name="Old Tom Distillery",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General, women should not drink alcoholic beverages during pregnancy.",
        extraction_confidence=0.95,
    )
    files = [("files", ("label.png", _sample_png(), "image/png"))]
    data = {"application": VALID_APPLICATION}
    with patch("app.main.extract_label_fields", return_value=fake_extracted):
        first = client.post("/api/review", files=files, data=data)
        assert first.status_code == 200
        bypass = client.post("/api/review?additional=0", files=files, data=data)
    assert bypass.status_code == 429
    assert bypass.json()["error_code"] == "RATE_LIMITED"


def test_daily_cap_uses_utc_for_reset_and_retry_after():
    """Retry-After and the in-memory day key share the same UTC calendar date."""
    daily_spend_guard.reset_for_test()
    today = utc_today()
    retry = daily_cap_retry_after_seconds()
    assert daily_spend_guard._current_date == today
    assert 1 <= retry <= 24 * 60 * 60
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    reset_at = dt.datetime(
        today.year, today.month, today.day, tzinfo=dt.timezone.utc
    ) + dt.timedelta(days=1)
    expected = max(1, int((reset_at - now).total_seconds()))
    assert abs(retry - expected) <= 2


def test_rejected_upload_does_not_count_toward_daily_spend(client: TestClient):
    """Rejected uploads must not increment the daily spend guard; valid ones do."""
    rejected = client.post(
        "/api/review",
        files=[("files", ("notes.jpg", b"just some text", "image/jpeg"))],
        data={"application": VALID_APPLICATION},
    )
    assert rejected.status_code == 200
    assert rejected.json()["overall_status"] == "error"
    assert rejected.json()["error_code"] == "UNSUPPORTED_FILE"
    assert "supported image" in (rejected.json().get("error") or "").lower()
    assert daily_spend_guard.get_count() == 0

    bad_json = client.post(
        "/api/review",
        files=[("files", ("label.png", _sample_png(), "image/png"))],
        data={"application": "not-json"},
    )
    assert bad_json.status_code == 422
    assert daily_spend_guard.get_count() == 0

    fake_extracted = ExtractedLabelData(
        brand_name="Old Tom Distillery",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General, women should not drink alcoholic beverages during pregnancy.",
        extraction_confidence=0.95,
    )
    with patch("app.main.extract_label_fields", return_value=fake_extracted):
        accepted = client.post(
            "/api/review",
            files=[("files", ("label.png", _sample_png(), "image/png"))],
            data={"application": VALID_APPLICATION},
        )
    assert accepted.status_code == 200
    assert daily_spend_guard.get_count() == 1


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
        assert "/var/" not in err
        assert "error occurred while reviewing this label" in err.lower()
        assert result_line["result"]["overall_status"] == "error"
        assert result_line["result"]["error_code"] == "INTERNAL_ERROR"
        assert result_line["result"].get("request_id")
