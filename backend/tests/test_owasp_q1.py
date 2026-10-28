"""OWASP Q1 API-hardening pack: request limits, auth lockout, docs gate, retries."""

from __future__ import annotations

import io
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from PIL.Image import DecompressionBombError

from app import auth as auth_module
from app import claude_client
from app.limiter import DailyCapExceeded, daily_spend_guard
from app.main import (
    _extract_fields_with_retry,
    api_docs_enabled,
    app,
)
from app.models import ExtractedLabelData
from app.request_limits import (
    MAX_IMAGES_PER_LABEL,
    read_upload_capped,
    validate_label_upload_request as shared_validate,
)
from app.security import API_CSP

VALID_APPLICATION = json.dumps({
    "beverage_type": "distilled_spirits",
    "brand_name": "Old Tom Distillery",
    "class_type": "Bourbon",
    "alcohol_content": "45% ABV",
    "net_contents": "750 mL",
})


def _png(width: int = 120, height: int = 120) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (80, 120, 160)).save(buf, format="PNG")
    return buf.getvalue()


def _extraction() -> ExtractedLabelData:
    return ExtractedLabelData(
        brand_name="Old Tom Distillery",
        class_type="Bourbon",
        alcohol_content="45% ABV",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General...",
        extraction_confidence=0.95,
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_docs_disabled_by_default(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    assert api_docs_enabled() is False
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_api_docs_enabled_helper(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENABLE_API_DOCS", "true")
    assert api_docs_enabled() is True


def test_demo_info_omits_username(client: TestClient):
    resp = client.get("/api/demo-info")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"auth_enabled": False} or set(body.keys()) == {"auth_enabled"}
    assert "username" not in body
    assert resp.headers.get("content-security-policy") == API_CSP


def test_health_stays_public_during_auth_lockout(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DEMO_ACCESS_TOKEN", "lockout-token")
    monkeypatch.setenv("AUTH_LOCKOUT_FAILURES", "3")
    monkeypatch.setenv("AUTH_LOCKOUT_WINDOW_SECONDS", "600")
    auth_module.auth_failure_guard.reset_for_test()
    files = [("files", ("label.png", _png(), "image/png"))]
    data = {"application": VALID_APPLICATION}
    for _ in range(3):
        denied = client.post("/api/review", files=files, data=data)
        assert denied.status_code == 401
    locked = client.post("/api/review", files=files, data=data)
    assert locked.status_code == 429
    assert locked.json()["error_code"] == "RATE_LIMITED"
    assert locked.headers.get("retry-after")
    health = client.get("/api/health")
    assert health.status_code == 200
    info = client.get("/api/demo-info")
    assert info.status_code == 200
    assert "username" not in info.json()


def test_auth_lockout_fails_open_on_counter_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DEMO_ACCESS_TOKEN", "ok-token")
    with patch.object(
        auth_module.auth_failure_guard, "is_locked_out", side_effect=RuntimeError("store down")
    ):
        resp = client.post(
            "/api/review",
            files=[("files", ("label.png", _png(), "image/png"))],
            data={"application": VALID_APPLICATION},
            headers={"Authorization": "Bearer ok-token"},
        )
    assert resp.status_code != 429


def test_invalid_application_does_not_echo_parser_text(client: TestClient):
    res = client.post(
        "/api/review",
        files=[("files", ("front.png", _png(), "image/png"))],
        data={"application": "not-json"},
    )
    assert res.status_code == 422
    dumped = json.dumps(res.json()).lower()
    assert "json" not in dumped or "invalid application data" in dumped
    assert "application" in dumped
    assert "expecting" not in dumped
    assert "traceback" not in dumped
    assert res.json()["request_id"]


def test_body_size_middleware_returns_413(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "200")
    from app.request_limits import get_max_request_body_bytes

    assert get_max_request_body_bytes() == 200
    # Recreate middleware uses env at request time, so a payload larger than 200
    # with Content-Length set by TestClient should 413. Health stays 200.
    health = client.get("/api/health")
    assert health.status_code == 200
    resp = client.post(
        "/api/review",
        files=[("files", ("front.png", _png(), "image/png"))],
        data={"application": VALID_APPLICATION},
    )
    assert resp.status_code == 413
    assert resp.json()["error_code"] == "FILE_TOO_LARGE"
    assert resp.json()["request_id"]


def test_max_labels_per_batch_rejected_before_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MAX_LABELS_PER_BATCH", "1")
    application = json.loads(VALID_APPLICATION)
    with patch("app.main.extract_label_fields") as extract:
        resp = client.post(
            "/api/review/batch",
            files=[
                ("files", ("a.png", _png(), "image/png")),
                ("files", ("b.png", _png(), "image/png")),
            ],
            data={
                "image_counts": "[1,1]",
                "applications": json.dumps([application, application]),
            },
        )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "INVALID_REQUEST"
    extract.assert_not_called()


def test_stream_rejects_too_many_images_per_label_before_read(
    client: TestClient,
):
    application = json.loads(VALID_APPLICATION)
    files = [
        ("files", (f"l{i}.png", _png(), "image/png"))
        for i in range(MAX_IMAGES_PER_LABEL + 1)
    ]
    with patch("app.main.read_upload_capped", new_callable=AsyncMock) as reader:
        resp = client.post(
            "/api/review/batch/stream",
            files=files,
            data={
                "image_counts": f"[{MAX_IMAGES_PER_LABEL + 1}]",
                "applications": json.dumps([application]),
            },
        )
    assert resp.status_code == 422
    assert "images" in resp.json()["message"].lower() or "images" in resp.json()["detail"].lower()
    reader.assert_not_called()


def test_shared_validator_used_by_sync_and_batch():
    class FakeFile:
        filename = "x.png"

    too_many = [FakeFile() for _ in range(MAX_IMAGES_PER_LABEL + 1)]
    rejected = shared_validate(too_many)
    assert rejected.error is not None
    assert rejected.error.code == "INVALID_REQUEST"
    ok = shared_validate(too_many[:1])
    assert ok.ok
    assert ok.label_count == 1


def test_anthropic_sdk_retries_disabled():
    assert getattr(claude_client._CLIENT, "max_retries") == 0


@pytest.mark.asyncio
async def test_app_retries_transient_error_once_with_jitter(monkeypatch: pytest.MonkeyPatch):
    calls = {"n": 0}

    class _Timeout(claude_client.APITimeoutError):
        def __init__(self):
            Exception.__init__(self, "timeout")

    def flaky(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Timeout()
        return _extraction()

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("app.main.extract_label_fields", flaky)
    monkeypatch.setattr("app.main.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.main.random.uniform", lambda _a, _b: 0.25)
    extracted, error = await _extract_fields_with_retry([(_png(), "label.png")])
    assert error is None
    assert extracted is not None
    assert calls["n"] == 2
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(2.25)


@pytest.mark.asyncio
async def test_app_retry_budget_is_one(monkeypatch: pytest.MonkeyPatch):
    class _Timeout(claude_client.APITimeoutError):
        def __init__(self):
            Exception.__init__(self, "timeout")

    monkeypatch.setattr(
        "app.main.extract_label_fields",
        lambda *_a, **_k: (_ for _ in ()).throw(_Timeout()),
    )
    monkeypatch.setattr("app.main.asyncio.sleep", AsyncMock())
    extracted, error = await _extract_fields_with_retry([(_png(), "label.png")])
    assert extracted is None
    assert error is not None
    assert error.code in {"UPSTREAM_TIMEOUT", "UPSTREAM_ERROR", "RATE_LIMITED"}


def test_decompression_bomb_is_error(monkeypatch: pytest.MonkeyPatch):
    def boom(_buf):
        raise DecompressionBombError("too many pixels")

    monkeypatch.setattr(Image, "open", boom)
    with pytest.raises(claude_client.ImageQualityError):
        claude_client.open_image_for_intake(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "huge.png")


def test_daily_cap_reserve_is_atomic(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DAILY_REQUEST_SOFT_CAP", "1")
    monkeypatch.setenv("MAX_REVIEW_REQUESTS_PER_DAY", "0")
    daily_spend_guard.reset_for_test()
    assert daily_spend_guard.reserve(1, client_ip="198.51.100.8") == 1
    with pytest.raises(DailyCapExceeded):
        daily_spend_guard.reserve(1, client_ip="198.51.100.8")
    assert daily_spend_guard.get_ip_count("198.51.100.8") == 1


def test_reserve_happens_before_model_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DAILY_REQUEST_SOFT_CAP", "1")
    monkeypatch.setenv("MAX_REVIEW_REQUESTS_PER_DAY", "0")
    monkeypatch.setenv("RATE_LIMIT_REVIEW", "100/minute")
    daily_spend_guard.reset_for_test()
    daily_spend_guard.reserve(1, client_ip="testclient")
    with patch("app.main.extract_label_fields") as extract:
        resp = client.post(
            "/api/review",
            files=[("files", ("label.png", _png(), "image/png"))],
            data={"application": VALID_APPLICATION},
        )
    assert resp.status_code == 429
    extract.assert_not_called()


def test_cap_trip_logs_structured_event(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DAILY_REQUEST_SOFT_CAP", "1")
    monkeypatch.setenv("MAX_REVIEW_REQUESTS_PER_DAY", "0")
    daily_spend_guard.reset_for_test()
    daily_spend_guard.reserve(1, client_ip="198.51.100.9")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(DailyCapExceeded):
            daily_spend_guard.reserve(1, client_ip="198.51.100.9")
    assert any("cap.daily_soft" in rec.message for rec in caplog.records)


def test_parse_template_rejects_oversize(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MAX_TEMPLATE_BYTES", "64")
    huge = b"x" * 200
    resp = client.post(
        "/api/cola/parse-template",
        files={"file": ("big.csv", huge, "text/csv")},
    )
    assert resp.status_code == 413
    assert resp.json()["error_code"] == "FILE_TOO_LARGE"
    dumped = json.dumps(resp.json()).lower()
    assert "traceback" not in dumped


@pytest.mark.asyncio
async def test_chunked_read_stops_at_max():
    class FakeUpload:
        filename = "huge.png"

        def __init__(self) -> None:
            self._chunks = [b"a" * 40, b"b" * 40, b"c" * 40]
            self.reads = 0

        async def read(self, n: int = -1) -> bytes:
            self.reads += 1
            if not self._chunks:
                return b""
            return self._chunks.pop(0)

    uploaded = FakeUpload()
    data, exceeded = await read_upload_capped(uploaded, max_size=50)
    assert exceeded is True
    assert len(data) <= 50
    assert uploaded.reads >= 2
