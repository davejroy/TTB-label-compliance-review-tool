"""Latency round 1: resize, prompt cache, output cap, and timings_ms.

No live Anthropic calls. Accuracy-sensitive fields stay on the same schema;
type-size still uses the original full-resolution JPEG.
"""

from __future__ import annotations

import io
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app.claude_client import (
    CACHED_RULE_TEXT,
    CACHE_CONTROL_EPHEMERAL,
    EXTRACTION_MAX_TOKENS,
    EXTRACTION_TOOL,
    JPEG_QUALITY,
    MAX_IMAGE_DIMENSION,
    PREVIOUS_EXTRACTION_MAX_TOKENS,
    SYSTEM_PROMPT,
    build_cached_extraction_tools,
    build_cached_system_blocks,
    build_extraction_create_kwargs,
    encode_full_resolution_jpeg,
    extract_label_fields,
    open_image_for_intake,
    prepare_image,
)
from app.compliance import CANONICAL_WARNING_BODY, CANONICAL_WARNING_HEADER
from app.main import app
from app.models import ExtractedLabelData, StageTimings


def _jpeg_bytes(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _tool_input() -> dict:
    return {
        "brand_name": "OLD TOM DISTILLERY",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "alcohol_content": "45% Alc./Vol.",
        "net_contents": "750 mL",
        "name_and_address": "Bottled by Old Tom Distillery, Louisville, KY",
        "country_of_origin": "",
        "government_warning_header": CANONICAL_WARNING_HEADER,
        "government_warning_body": CANONICAL_WARNING_BODY,
        "government_warning_present": True,
        "government_warning_header_appears_bold": True,
        "beverage_type_guess": "distilled_spirits",
        "origin_guess": "domestic",
        "field_locations": [
            {
                "field": "government_warning",
                "image_index": 0,
                "x": 0.10,
                "y": 0.70,
                "width": 0.80,
                "height": 0.20,
                "confidence": "high",
            }
        ],
        "notes": "",
        "sulfite_declaration": "Contains Sulfites",
        "allergen_statements": "",
        "age_statement": "",
        "commodity_statement": "",
        "is_alcohol_beverage_label": True,
        "per_field_confidence": {
            "brand_name": 0.95,
            "class_type": 0.92,
            "alcohol_content": 0.90,
            "net_contents": 0.93,
            "name_and_address": 0.88,
            "country_of_origin": 0.0,
            "government_warning_header": 0.94,
            "government_warning_body": 0.62,
        },
        "extraction_confidence": 0.91,
    }


def _estimate_tokens(payload: dict) -> int:
    """Rough token count for compact tool JSON (≈ 4 chars / token)."""
    return max(1, len(json.dumps(payload, separators=(",", ":"))) // 4)


# ---------------------------------------------------------------------------
# Image sizing
# ---------------------------------------------------------------------------


def test_model_payload_long_edge_is_1568_at_jpeg_85():
    """Vision payload is Anthropic's 1568 px long-edge cap at JPEG ~85."""
    assert MAX_IMAGE_DIMENSION == 1568
    assert JPEG_QUALITY == 85
    big = Image.new("RGB", (4000, 3000), (180, 160, 140))
    out_bytes, mime = prepare_image(_jpeg_bytes(big), "big.jpg")
    assert mime == "image/jpeg"
    out = Image.open(io.BytesIO(out_bytes))
    assert max(out.size) == 1568
    assert out.format == "JPEG"


def test_type_size_source_stays_full_resolution_after_model_resize():
    """Measurement path keeps the EXIF-corrected original, not the 1568 payload."""
    src = Image.new("RGB", (3200, 2400), (40, 80, 120))
    opened = open_image_for_intake(_jpeg_bytes(src), "label.jpg")
    processed, _ = prepare_image(_jpeg_bytes(src), "label.jpg", image=opened)
    original = encode_full_resolution_jpeg(opened)
    assert max(Image.open(io.BytesIO(processed)).size) == 1568
    assert Image.open(io.BytesIO(original)).size == (3200, 2400)


def test_images_already_under_1568_are_not_upscaled():
    small = Image.new("RGB", (800, 600), (200, 200, 200))
    out_bytes, _ = prepare_image(_jpeg_bytes(small), "small.jpg")
    assert Image.open(io.BytesIO(out_bytes)).size == (800, 600)


# ---------------------------------------------------------------------------
# Output trim
# ---------------------------------------------------------------------------


def test_output_token_budget_sits_just_above_compact_p95():
    """Cap dropped from 1500 to 800; compact required JSON is well under 800."""
    assert PREVIOUS_EXTRACTION_MAX_TOKENS == 1500
    assert EXTRACTION_MAX_TOKENS == 800
    compact = _estimate_tokens(_tool_input())
    # Compact required JSON (warning + boxes + sulfites) is ~300 tokens.
    # 800 sits above a conservative p95 once tool-use wrappers and longer
    # addresses are included, and well below the previous 1500 cap.
    assert compact < 400
    assert EXTRACTION_MAX_TOKENS > compact
    assert EXTRACTION_MAX_TOKENS < PREVIOUS_EXTRACTION_MAX_TOKENS


def test_system_prompt_asks_for_compact_json_only():
    assert "compact JSON only" in SYSTEM_PROMPT
    assert "No prose" in SYSTEM_PROMPT
    assert "no restated label text" in SYSTEM_PROMPT


def test_extraction_schema_keeps_every_backend_read_field():
    """No field the backend reads was removed from the tool schema."""
    props = set(EXTRACTION_TOOL["input_schema"]["properties"])
    required_by_backend = {
        "brand_name",
        "class_type",
        "alcohol_content",
        "net_contents",
        "name_and_address",
        "country_of_origin",
        "government_warning_header",
        "government_warning_body",
        "government_warning_present",
        "government_warning_header_appears_bold",
        "beverage_type_guess",
        "origin_guess",
        "field_locations",
        "notes",
        "sulfite_declaration",
        "allergen_statements",
        "age_statement",
        "commodity_statement",
        "is_alcohol_beverage_label",
        "per_field_confidence",
        "extraction_confidence",
    }
    assert required_by_backend <= props


# ---------------------------------------------------------------------------
# Prompt caching
# ---------------------------------------------------------------------------


def test_cache_control_covers_system_cfr_warning_and_schema():
    """Static prefix is ephemeral-cached at the last system block and tool."""
    system = build_cached_system_blocks()
    assert len(system) == 2
    assert system[0]["text"] == SYSTEM_PROMPT
    assert system[1]["text"] == CACHED_RULE_TEXT
    assert CANONICAL_WARNING_HEADER in CACHED_RULE_TEXT
    assert CANONICAL_WARNING_BODY in CACHED_RULE_TEXT
    assert "27 CFR 16.21" in CACHED_RULE_TEXT
    assert "cache_control" not in system[0]
    assert system[1]["cache_control"] == CACHE_CONTROL_EPHEMERAL

    tools = build_cached_extraction_tools()
    assert tools[0]["name"] == "record_label_fields"
    assert tools[0]["input_schema"] == EXTRACTION_TOOL["input_schema"]
    assert tools[0]["cache_control"] == CACHE_CONTROL_EPHEMERAL


def test_extract_kwargs_place_cache_before_images():
    image_blocks = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": "abc"},
        }
    ]
    kwargs = build_extraction_create_kwargs(image_blocks, "Record compact JSON.", "claude-sonnet-4-6")
    assert kwargs["max_tokens"] == EXTRACTION_MAX_TOKENS
    assert "cache_control" not in kwargs["system"][0]
    assert kwargs["system"][-1]["cache_control"]["type"] == "ephemeral"
    assert kwargs["tools"][0]["cache_control"]["type"] == "ephemeral"
    user_content = kwargs["messages"][0]["content"]
    assert user_content[0]["type"] == "image"
    assert user_content[-1]["type"] == "text"
    assert "cache_control" not in user_content[0]
    # Identical-image replay caches the vision prefix at the last user block.
    assert user_content[-1]["cache_control"] == CACHE_CONTROL_EPHEMERAL


def test_extract_label_fields_sends_cached_request(monkeypatch):
    from types import SimpleNamespace

    captured = {}

    def _create(**kwargs):
        captured.update(kwargs)
        block = SimpleNamespace(type="tool_use", name="record_label_fields", input=_tool_input())
        return SimpleNamespace(
            content=[block],
            usage=SimpleNamespace(
                input_tokens=2000,
                output_tokens=520,
                cache_read_input_tokens=1800,
                cache_creation_input_tokens=0,
            ),
            model="claude-sonnet-4-6",
            id="msg_cache",
            _request_id="req_cache",
        )

    monkeypatch.setattr("app.claude_client._CLIENT.messages.create", _create)
    result = extract_label_fields([(b"jpeg", "label.jpg")], preprocessed=True)
    assert result.brand_name == "OLD TOM DISTILLERY"
    assert captured["max_tokens"] == EXTRACTION_MAX_TOKENS
    assert captured["system"][-1]["cache_control"] == CACHE_CONTROL_EPHEMERAL
    assert captured["tools"][0]["cache_control"] == CACHE_CONTROL_EPHEMERAL
    assert captured["messages"][0]["content"][-1]["cache_control"] == CACHE_CONTROL_EPHEMERAL


# ---------------------------------------------------------------------------
# timings_ms
# ---------------------------------------------------------------------------


def test_review_response_includes_timings_ms(caplog: pytest.LogCaptureFixture):
    client = TestClient(app)
    fake = ExtractedLabelData(**_tool_input())
    with caplog.at_level(logging.INFO):
        with patch("app.main._extract_fields_with_retry", AsyncMock(return_value=(fake, None))):
            resp = client.post(
                "/api/review",
                files=[("files", ("label.jpg", _jpeg_bytes(Image.new("RGB", (200, 160), (90, 90, 90))), "image/jpeg"))],
                data={
                    "application": json.dumps(
                        {
                            "beverage_type": "distilled_spirits",
                            "brand_name": "OLD TOM DISTILLERY",
                            "class_type": "Kentucky Straight Bourbon Whiskey",
                            "alcohol_content": "45% Alc./Vol.",
                            "net_contents": "750 mL",
                        }
                    )
                },
            )
    assert resp.status_code == 200
    body = resp.json()
    timings = body["timings_ms"]
    assert {
        "decode",
        "pre_gate",
        "resize",
        "model",
        "compliance",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "model_ttft_ms",
        "model_generation_ms",
    } <= set(timings)
    for key, value in timings.items():
        assert isinstance(value, int)
        assert value >= 0
    # Intake (decode / pre-gate / resize) ran on a real JPEG.
    assert timings["decode"] + timings["pre_gate"] + timings["resize"] >= 0
    assert isinstance(StageTimings(**timings), StageTimings)

    stage_logs = [r.message for r in caplog.records if "StageTimings:" in r.message]
    assert stage_logs
    logged = json.loads(stage_logs[0].replace("StageTimings: ", "", 1))
    assert logged["event"] == "stage_timings"
    assert logged["decode"] == timings["decode"]
    assert logged["model"] == timings["model"]

    request_logs = [r.message for r in caplog.records if "RequestTiming:" in r.message]
    assert request_logs
    req = json.loads(request_logs[0].replace("RequestTiming: ", "", 1))
    assert req["timings_ms"]["compliance"] == timings["compliance"]


def test_label_check_timings_ms_is_additive():
    client = TestClient(app)
    fake = ExtractedLabelData(**_tool_input())
    with patch("app.main._extract_fields_with_retry", AsyncMock(return_value=(fake, None))):
        resp = client.post(
            "/api/label-check/batch",
            files=[("files", ("label.jpg", _jpeg_bytes(Image.new("RGB", (180, 140), (80, 80, 80))), "image/jpeg"))],
            data={
                "image_counts": "[1]",
                "confirmed_beverage_type": "distilled_spirits",
            },
        )
    assert resp.status_code == 200
    body = resp.json()[0]
    assert "overall_status" in body
    assert "timings_ms" in body
    assert body["processing_time_ms"] >= 0
    assert body["timings_ms"]["model"] >= 0
