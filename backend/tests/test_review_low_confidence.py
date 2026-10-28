"""Regression tests for missing-field handling on review routes.

When a readable photo has an empty required field scored 0.0, the field is
absent — a compliance fail of "X not found on label" — not a photo retake.
Review endpoints must return HTTP 200 (never 500).
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
        "name_and_address": "Bottled by Bourbon Co, Louisville, KY",
    }


def _missing_address_extraction() -> ExtractedLabelData:
    """Readable photo: bottler name/address is not printed (empty + 0.0)."""
    return ExtractedLabelData(
        brand_name="BOURBON CO",
        class_type="Whiskey",
        alcohol_content="45% ABV",
        net_contents="750 mL",
        name_and_address="",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        extraction_confidence=0.95,
        per_field_confidence={"name_and_address": 0.0},
    )


@pytest.fixture
def client():
    return TestClient(main_module.app)


def _assert_missing_field_fail(payload: dict) -> None:
    """Shared assertions: missing address is a fail, never a 500 or retake."""
    assert payload["overall_status"] == "fail"
    assert payload.get("error_code") not in {"PHOTO_UNREADABLE_FIELD", "PHOTO_QUALITY_LOW"}
    field_status = payload.get("field_status") or {}
    assert field_status.get("name_and_address") == "absent"
    field_names = {item["field"] for item in payload.get("fields") or []}
    assert "brand_name" in field_names
    assert "name_and_address" in field_names
    addr = next(item for item in payload["fields"] if item["field"] == "name_and_address")
    assert addr["status"] == "fail"
    assert "not found on label" in addr["message"].lower()
    extracted = payload.get("extracted") or {}
    assert extracted.get("per_field_confidence", {}).get("name_and_address") == 0.0
    assert payload.get("request_id")


def test_review_single_missing_required_field_fails_not_retake(client):
    """POST /api/review fails the missing field, never HTTP 500 or a retake."""
    with patch(
        "app.main._extract_fields_with_retry",
        AsyncMock(return_value=(_missing_address_extraction(), None)),
    ):
        res = client.post(
            "/api/review",
            files=[("files", ("front.png", _sample_png(), "image/png"))],
            data={"application": json.dumps(_application())},
        )

    assert res.status_code == 200
    assert res.status_code not in (401, 403, 500)
    _assert_missing_field_fail(res.json())


def test_review_batch_missing_required_field_fails_not_retake(client):
    """POST /api/review/batch uses _review_single and must not 500."""
    with patch(
        "app.main._extract_fields_with_retry",
        AsyncMock(return_value=(_missing_address_extraction(), None)),
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
    _assert_missing_field_fail(body[0])


def test_review_batch_stream_missing_required_field_fails_not_retake(client):
    """POST /api/review/batch/stream reports the missing field in NDJSON."""
    with patch(
        "app.main.extract_label_fields",
        return_value=_missing_address_extraction(),
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
    _assert_missing_field_fail(result_line["result"])
