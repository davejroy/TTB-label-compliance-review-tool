"""Regression tests for LowConfidenceError handling on review routes.

When extraction cannot read a required field (for example a front-only photo
with no bottler name/address), run_compliance_checks raises LowConfidenceError.
Review endpoints must return the user-facing retake message in the result body
(HTTP 200, overall_status=fail) rather than an unhandled HTTP 500.
"""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
from app.compliance import CANONICAL_WARNING_BODY, CANONICAL_WARNING_HEADER
from app.models import ExtractedLabelData


def _sample_png(width: int = 100, height: int = 100) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (100, 150, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _application() -> dict:
    return {
        "beverage_type": "distilled_spirits",
        "brand_name": "BOURBON CO",
        "class_type": "Whiskey",
        "alcohol_content": "45% ABV",
        "net_contents": "750 mL",
    }


def _low_confidence_extraction() -> ExtractedLabelData:
    """Front-only extraction: required name_and_address is unreadable (0%)."""
    return ExtractedLabelData(
        brand_name="BOURBON CO",
        class_type="Whiskey",
        alcohol_content="45% ABV",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        extraction_confidence=0.95,
        per_field_confidence={"name_and_address": 0.0},
    )


@pytest.fixture
def client():
    return TestClient(main_module.app)


def _assert_retake_result(payload: dict) -> None:
    """Shared assertions for the low-confidence retake response body."""
    assert payload["overall_status"] == "fail"
    assert payload["fields"] == []
    error = payload.get("error") or ""
    assert error, "expected a user-facing error message"
    assert "retake" in error.lower()
    assert "name and address" in error.lower() or "bottler" in error.lower()
    assert "500" not in error
    extracted = payload.get("extracted") or {}
    assert extracted.get("per_field_confidence", {}).get("name_and_address") == 0.0


def test_review_single_low_confidence_required_field_returns_retake_message(client):
    """POST /api/review returns the retake-photo message, never HTTP 500."""
    with patch(
        "app.main._extract_fields_with_retry",
        AsyncMock(return_value=(_low_confidence_extraction(), None)),
    ):
        res = client.post(
            "/api/review",
            files=[("files", ("front.png", _sample_png(), "image/png"))],
            data={"application": json.dumps(_application())},
        )

    assert res.status_code == 200
    assert res.status_code not in (401, 403, 500)
    _assert_retake_result(res.json())


def test_review_batch_low_confidence_required_field_returns_retake_message(client):
    """POST /api/review/batch uses _review_single and must not 500."""
    with patch(
        "app.main._extract_fields_with_retry",
        AsyncMock(return_value=(_low_confidence_extraction(), None)),
    ):
        res = client.post(
            "/api/review/batch",
            files=[("files", ("front.png", _sample_png(), "image/png"))],
            data={
                "image_counts": "[1]",
                "applications": json.dumps([_application()]),
            },
        )

    assert res.status_code == 200
    assert res.status_code not in (401, 403, 500)
    body = res.json()
    assert isinstance(body, list)
    assert len(body) == 1
    _assert_retake_result(body[0])


def test_review_batch_stream_low_confidence_required_field_returns_retake_message(client):
    """POST /api/review/batch/stream returns the retake message in NDJSON."""
    with patch(
        "app.main._extract_fields_with_retry",
        AsyncMock(return_value=(_low_confidence_extraction(), None)),
    ):
        res = client.post(
            "/api/review/batch/stream",
            files=[("files", ("front.png", _sample_png(), "image/png"))],
            data={
                "image_counts": "[1]",
                "applications": json.dumps([_application()]),
            },
        )

    assert res.status_code == 200
    assert res.status_code not in (401, 403, 500)
    lines = [json.loads(line) for line in res.text.strip().split("\n") if line.strip()]
    assert lines, "expected NDJSON result lines"
    result_line = next(item for item in lines if "result" in item)
    _assert_retake_result(result_line["result"])
