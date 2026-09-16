"""Tests for performance optimizations and structured request timing logging.

Verifies:
1. Structured latency and request timing logging format and safety (no secrets/image bytes).
2. Multi-photo concurrent extraction via asyncio.gather and merge semantics.
3. Batch endpoint latency logging.
"""

from __future__ import annotations

import io
import json
import logging
import pytest
from unittest.mock import AsyncMock, patch
from PIL import Image

from app.compliance import CANONICAL_WARNING_BODY, CANONICAL_WARNING_HEADER
from app.main import (
    ExtractionMetrics,
    _extract_fields,
    _extract_fields_with_retry,
    _log_request_timing,
    app,
)
from app.models import ApplicationData, ExtractedLabelData, FieldLocation
from fastapi.testclient import TestClient


def _sample_png(width: int = 150, height: int = 150) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 180, 240)).save(buf, format="PNG")
    return buf.getvalue()


def test_log_request_timing_structure_and_safety(caplog: pytest.LogCaptureFixture) -> None:
    """Verify _log_request_timing outputs valid JSON with expected metrics without leaking secrets."""
    with caplog.at_level(logging.INFO):
        _log_request_timing(
            endpoint="/api/review",
            wall_time_ms=1234,
            claude_time_ms=950,
            claude_calls=1,
            image_count=2,
            status="pass",
            extra={"test_field": "val"},
        )

    assert len(caplog.records) >= 1
    rec = [r for r in caplog.records if "RequestTiming:" in r.message][0]
    json_str = rec.message.replace("RequestTiming: ", "")
    data = json.loads(json_str)

    assert data["event"] == "request_timing"
    assert data["endpoint"] == "/api/review"
    assert data["wall_time_ms"] == 1234
    assert data["claude_time_ms"] == 950
    assert data["claude_calls"] == 1
    assert data["image_count"] == 2
    assert data["status"] == "pass"
    assert data["test_field"] == "val"
    # Ensure no secrets or raw byte objects are present in log record
    assert "token" not in data
    assert "api_key" not in data


@pytest.mark.asyncio
async def test_multi_photo_concurrent_extraction_executes_in_parallel() -> None:
    """Verify that multi-photo extractions with distinct roles run concurrently via gather."""
    front_data = ExtractedLabelData(
        brand_name="BOURBON VALLEY",
        class_type="Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
        extraction_confidence=0.95,
        per_field_confidence={"brand_name": 0.95, "class_type": 0.90},
    )
    back_data = ExtractedLabelData(
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        name_and_address="Distilled and Bottled by Bourbon Valley Co., Louisville, KY",
        extraction_confidence=0.92,
        per_field_confidence={"government_warning_header": 0.95, "name_and_address": 0.90},
    )

    call_order = []

    async def mock_extract_with_retry(images, metrics=None):
        call_order.append(len(images))
        if metrics is not None:
            metrics.claude_calls += 1
            metrics.claude_time_ms += 100
        # Return front or back based on call count
        if len(call_order) == 1:
            return front_data, None
        return back_data, None

    img1 = (_sample_png(), "front.png")
    img2 = (_sample_png(), "back.png")

    with patch("app.main._read_images", AsyncMock(return_value=[img1, img2])):
        with patch("app.main._extract_fields_with_retry", side_effect=mock_extract_with_retry):
            # Mock UploadFile objects
            class FakeUpload:
                filename = "fake.png"

            metrics = ExtractionMetrics()
            merged, err, roles = await _extract_fields(
                [FakeUpload(), FakeUpload()],
                ["front.png", "back.png"],
                photo_roles=["front", "back"],
                metrics=metrics,
            )

            assert err is None
            assert merged is not None
            assert merged.brand_name == "BOURBON VALLEY"
            assert merged.government_warning_present is True
            assert merged.government_warning_header == CANONICAL_WARNING_HEADER
            assert roles == ["front", "back"]
            assert metrics.claude_calls == 2
            assert metrics.claude_time_ms == 200


def test_review_endpoint_logs_request_timing(caplog: pytest.LogCaptureFixture) -> None:
    """Verify /api/review endpoint generates structured RequestTiming log."""
    client = TestClient(app)
    app_json = json.dumps({
        "beverage_type": "distilled_spirits",
        "brand_name": "Test Brand",
        "class_type": "Whiskey",
        "alcohol_content": "40% ABV",
        "net_contents": "750 mL",
    })

    fake_ext = ExtractedLabelData(
        brand_name="Test Brand",
        class_type="Whiskey",
        alcohol_content="40% ABV",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        extraction_confidence=0.95,
    )

    with caplog.at_level(logging.INFO):
        with patch("app.main._extract_fields_with_retry", AsyncMock(return_value=(fake_ext, None))):
            resp = client.post(
                "/api/review",
                files=[("files", ("label.png", _sample_png(), "image/png"))],
                data={"application": app_json},
            )
            assert resp.status_code == 200
            assert resp.json()["overall_status"] == "pass"

    timing_logs = [r.message for r in caplog.records if "RequestTiming:" in r.message]
    assert len(timing_logs) >= 1
    log_data = json.loads(timing_logs[0].replace("RequestTiming: ", ""))
    assert log_data["endpoint"] == "/api/review"
    assert log_data["status"] == "pass"
    assert log_data["image_count"] == 1
    assert "wall_time_ms" in log_data
    assert "claude_time_ms" in log_data
    assert "claude_calls" in log_data


def test_label_check_batch_logs_request_timing(caplog: pytest.LogCaptureFixture) -> None:
    """Verify /api/label-check/batch generates structured RequestTiming logs."""
    client = TestClient(app)

    fake_ext = ExtractedLabelData(
        brand_name="Test Brand",
        class_type="Whiskey",
        alcohol_content="40% ABV",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        beverage_type_guess="distilled_spirits",
        is_alcohol_beverage_label=True,
        extraction_confidence=0.95,
        per_field_confidence={"brand_name": 0.95, "alcohol_content": 0.95},
    )

    with caplog.at_level(logging.INFO):
        with patch("app.main._extract_fields_with_retry", AsyncMock(return_value=(fake_ext, None))):
            resp = client.post(
                "/api/label-check/batch",
                files=[
                    ("files", ("front.png", _sample_png(), "image/png")),
                    ("files", ("back.png", _sample_png(), "image/png")),
                ],
                data={
                    "image_counts": "[2]",
                    "photo_roles": json.dumps(["front", "back"]),
                    "confirmed_beverage_type": "distilled_spirits",
                },
            )
            assert resp.status_code == 200

    timing_logs = [r.message for r in caplog.records if "RequestTiming:" in r.message]
    assert len(timing_logs) >= 1
    batch_log = [l for l in timing_logs if "/api/label-check/batch" in l][0]
    batch_data = json.loads(batch_log.replace("RequestTiming: ", ""))
    assert batch_data["endpoint"] == "/api/label-check/batch"
    assert batch_data["status"] == "success"
    assert batch_data["image_count"] == 2
    assert batch_data["label_count"] == 1
