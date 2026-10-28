"""Regression tests for prepare_image unpacking.

prepare_image returns (processed_bytes, media_type). _read_and_validate_file
wraps that pair in a PreparedImage. Callers must pass JPEG bytes, not the
PreparedImage object, to extract_label_fields.

These tests run the real _read_and_validate_file / prepare_image path and
mock only the Anthropic client.
"""

from __future__ import annotations

import base64
import io
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from PIL import Image

from app.claude_client import extract_label_fields as real_extract_label_fields
from app.compliance import CANONICAL_WARNING_BODY, CANONICAL_WARNING_HEADER
from app.main import PreparedImage, _read_and_validate_file, app

VALID_APPLICATION = json.dumps({
    "beverage_type": "distilled_spirits",
    "brand_name": "Old Tom Distillery",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
})

EXTRACTED_FIELDS = {
    "brand_name": "Old Tom Distillery",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
    "name_and_address": "Old Tom Distillery, Louisville, KY",
    "country_of_origin": "",
    "government_warning_header": CANONICAL_WARNING_HEADER,
    "government_warning_body": CANONICAL_WARNING_BODY,
    "government_warning_present": True,
    "beverage_type_guess": "distilled_spirits",
    "origin_guess": "domestic",
    "field_locations": [],
    "notes": "",
    "sulfite_declaration": "",
    "allergen_statements": "",
    "age_statement": "",
    "commodity_statement": "",
    "is_alcohol_beverage_label": True,
    "per_field_confidence": {},
    "extraction_confidence": 0.95,
}


def _sample_png(width: int = 200, height: int = 200) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (100, 150, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _mock_anthropic_response() -> MagicMock:
    mock_response = MagicMock()
    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.name = "record_label_fields"
    mock_block.input = EXTRACTED_FIELDS
    mock_response.content = [mock_block]
    return mock_response


def _assert_extract_received_jpeg_bytes(extract_spy: MagicMock) -> None:
    assert extract_spy.called, "extract_label_fields was not called"
    images = extract_spy.call_args.args[0]
    assert images, "extract_label_fields received no images"
    for image_bytes, filename in images:
        assert isinstance(image_bytes, bytes), (
            f"extract_label_fields expected image bytes, got {type(image_bytes).__name__}"
        )
        assert image_bytes.startswith(b"\xff\xd8"), "prepared image is not JPEG bytes"
        assert isinstance(filename, str)


def _assert_anthropic_received_jpeg_payload(create_mock: MagicMock) -> None:
    assert create_mock.called, "Anthropic client was not called"
    _, kwargs = create_mock.call_args
    content = kwargs["messages"][0]["content"]
    image_blocks = [block for block in content if block.get("type") == "image"]
    assert image_blocks, "Anthropic request contained no image blocks"
    decoded = base64.b64decode(image_blocks[0]["source"]["data"])
    assert decoded.startswith(b"\xff\xd8")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.asyncio
async def test_read_and_validate_file_returns_prepared_bytes_not_tuple() -> None:
    """_read_and_validate_file must unpack prepare_image and return JPEG bytes."""
    upload = UploadFile(filename="label.png", file=io.BytesIO(_sample_png()))
    result = await _read_and_validate_file(upload)

    assert isinstance(result, PreparedImage), f"validation failed: {result}"
    assert isinstance(result.processed_bytes, bytes)
    assert result.processed_bytes.startswith(b"\xff\xd8")
    assert isinstance(result.filename, str)


def test_single_review_passes_prepared_bytes_to_extract_label_fields(client: TestClient) -> None:
    """Single-label review must send JPEG bytes, not a prepare_image tuple."""
    with patch("app.main.extract_label_fields", wraps=real_extract_label_fields) as extract_spy:
        with patch(
            "app.claude_client._CLIENT.messages.create",
            return_value=_mock_anthropic_response(),
        ) as create_mock:
            resp = client.post(
                "/api/review",
                files=[("files", ("label.png", _sample_png(), "image/png"))],
                data={"application": VALID_APPLICATION},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body.get("error") is None
    assert "Unexpected error during label extraction" not in (body.get("error") or "")
    assert body["extracted"]["brand_name"] == "Old Tom Distillery"
    _assert_extract_received_jpeg_bytes(extract_spy)
    _assert_anthropic_received_jpeg_payload(create_mock)


def test_batch_stream_passes_prepared_bytes_to_extract_label_fields(client: TestClient) -> None:
    """Batch stream review must send JPEG bytes, not a prepare_image tuple."""
    with patch("app.main.extract_label_fields", wraps=real_extract_label_fields) as extract_spy:
        with patch(
            "app.claude_client._CLIENT.messages.create",
            return_value=_mock_anthropic_response(),
        ) as create_mock:
            resp = client.post(
                "/api/review/batch/stream",
                files=[("files", ("label.png", _sample_png(), "image/png"))],
                data={
                    "image_counts": "[1]",
                    "applications": json.dumps([json.loads(VALID_APPLICATION)]),
                },
            )

    assert resp.status_code == 200
    lines = [json.loads(line) for line in resp.text.strip().split("\n") if line.strip()]
    assert lines, "batch stream returned no lines"
    result = lines[0]["result"]
    assert result.get("error") is None
    assert "Unexpected error during label extraction" not in (result.get("error") or "")
    assert result["extracted"]["brand_name"] == "Old Tom Distillery"
    _assert_extract_received_jpeg_bytes(extract_spy)
    _assert_anthropic_received_jpeg_payload(create_mock)
