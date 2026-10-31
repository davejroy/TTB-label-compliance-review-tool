"""Error-contract and retake UX tests (no live vision API).

Covers request_id headers, status separation (retake vs fail vs error),
partial results, unreadable-vs-absent, and scrubbed user-facing messages.
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
from app.errors import (
    RETAKE_MESSAGE,
    ClientError,
    current_request_id,
    generate_request_id,
    scrub_user_message,
    set_request_id,
)
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
        "name_and_address": "123 Main St, Louisville, KY",
    }


def _readable_extraction(**overrides) -> ExtractedLabelData:
    data = dict(
        brand_name="BOURBON CO",
        class_type="Whiskey",
        alcohol_content="45% ABV",
        net_contents="750 mL",
        name_and_address="123 Main St, Louisville, KY",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        extraction_confidence=0.95,
        per_field_confidence={
            "brand_name": 0.95,
            "class_type": 0.9,
            "alcohol_content": 0.9,
            "net_contents": 0.9,
            "name_and_address": 0.9,
            "government_warning_body": 0.8,
        },
    )
    data.update(overrides)
    return ExtractedLabelData(**data)


@pytest.fixture
def client():
    return TestClient(main_module.app)


def test_request_id_header_on_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.headers.get("x-request-id")
    assert len(res.headers["x-request-id"]) >= 16


def test_request_id_echoes_incoming_header(client):
    res = client.get("/api/health", headers={"X-Request-ID": "client-trace-abc"})
    assert res.headers.get("x-request-id") == "client-trace-abc"


def test_request_id_rejects_unsafe_incoming_header(client):
    res = client.get("/api/health", headers={"X-Request-ID": "../../etc/passwd;drop"})
    echoed = res.headers.get("x-request-id") or ""
    assert echoed
    assert echoed != "../../etc/passwd;drop"
    assert all(ch.isalnum() or ch == "-" for ch in echoed)


def test_review_includes_request_id_in_body_and_header(client):
    with patch(
        "app.main._extract_fields_with_retry",
        AsyncMock(return_value=(_readable_extraction(), None)),
    ):
        res = client.post(
            "/api/review",
            files=[("files", ("front.png", _sample_png(), "image/png"))],
            data={"application": json.dumps(_application())},
        )
    assert res.status_code == 200
    body = res.json()
    assert res.headers.get("x-request-id")
    assert body["request_id"] == res.headers["x-request-id"]
    assert body["overall_status"] in {"pass", "warning", "fail"}
    assert body["error_code"] is None


def test_low_confidence_overall_returns_retake_not_fail(client):
    extracted = _readable_extraction(extraction_confidence=0.10, per_field_confidence={})
    with patch(
        "app.main._extract_fields_with_retry",
        AsyncMock(return_value=(extracted, None)),
    ):
        res = client.post(
            "/api/review",
            files=[("files", ("front.png", _sample_png(), "image/png"))],
            data={"application": json.dumps(_application())},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["overall_status"] == "retake"
    assert body["error_code"] == "PHOTO_QUALITY_LOW"
    assert body["error"] == RETAKE_MESSAGE
    assert body["retake"]["field"] == "label"
    assert body["retake"]["cause"] in {
        "blur",
        "glare",
        "blank",
        "dark",
        "angle",
        "curvature",
        "cropped",
        "low_resolution",
    }
    assert body["retake"]["tip"]
    assert body["field_status"]["brand_name"] == "unreadable"
    assert body["fields"] == []


def test_partial_results_keep_readable_findings(client):
    extracted = _readable_extraction(
        name_and_address="",
        per_field_confidence={
            "brand_name": 0.95,
            "class_type": 0.9,
            "alcohol_content": 0.9,
            "net_contents": 0.9,
            "name_and_address": 0.0,
            "government_warning_body": 0.8,
        },
    )
    with patch(
        "app.main._extract_fields_with_retry",
        AsyncMock(return_value=(extracted, None)),
    ):
        res = client.post(
            "/api/review",
            files=[("files", ("front.png", _sample_png(), "image/png"))],
            data={"application": json.dumps(_application())},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["overall_status"] == "fail"
    names = {item["field"] for item in body["fields"]}
    assert "brand_name" in names
    assert "alcohol_content" in names
    assert "name_and_address" in names
    assert body["field_status"]["name_and_address"] == "absent"
    assert body["field_status"]["brand_name"] == "present"
    addr = next(item for item in body["fields"] if item["field"] == "name_and_address")
    assert addr["status"] == "fail"
    assert "not found on label" in addr["message"].lower()


def test_unreadable_is_never_reported_as_absent(client):
    extracted = _readable_extraction(
        name_and_address=None,
        per_field_confidence={
            "brand_name": 0.95,
            "class_type": 0.9,
            "alcohol_content": 0.9,
            "net_contents": 0.9,
            "name_and_address": 0.05,
            "government_warning_body": 0.8,
        },
    )
    with patch(
        "app.main._extract_fields_with_retry",
        AsyncMock(return_value=(extracted, None)),
    ):
        res = client.post(
            "/api/review",
            files=[("files", ("front.png", _sample_png(), "image/png"))],
            data={"application": json.dumps(_application())},
        )
    body = res.json()
    assert body["field_status"]["name_and_address"] == "unreadable"
    assert body["field_status"]["name_and_address"] != "absent"
    for item in body["fields"]:
        assert item["field"] != "name_and_address"
        assert "was not found" not in item["message"].lower() or item["field"] != "name_and_address"


def test_compliance_fail_stays_fail(client):
    extracted = _readable_extraction(
        brand_name="OTHER BRAND",
        government_warning_present=False,
        government_warning_header="",
        government_warning_body="",
        per_field_confidence={
            "brand_name": 0.95,
            "class_type": 0.9,
            "alcohol_content": 0.9,
            "net_contents": 0.9,
            "government_warning_header": 0.0,
            "government_warning_body": 0.0,
        },
    )
    with patch(
        "app.main._extract_fields_with_retry",
        AsyncMock(return_value=(extracted, None)),
    ):
        res = client.post(
            "/api/review",
            files=[("files", ("front.png", _sample_png(), "image/png"))],
            data={"application": json.dumps(_application())},
        )
    body = res.json()
    assert res.status_code == 200
    assert body["overall_status"] == "fail"
    assert body["error_code"] is None
    gw = next(item for item in body["fields"] if item["field"] == "government_warning")
    assert gw["status"] == "fail"
    assert body["field_status"]["government_warning"] == "absent"


def test_unsupported_file_is_error_not_fail(client):
    res = client.post(
        "/api/review",
        files=[("files", ("notes.jpg", b"just some text", "image/jpeg"))],
        data={"application": json.dumps(_application())},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["overall_status"] == "error"
    assert body["error_code"] == "UNSUPPORTED_FILE"
    assert "traceback" not in (body["error"] or "").lower()
    assert res.headers.get("x-request-id") == body["request_id"]


def test_tiny_image_returns_retake_with_low_resolution_cause(client):
    tiny = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(tiny, format="PNG")
    res = client.post(
        "/api/review",
        files=[("files", ("tiny.png", tiny.getvalue(), "image/png"))],
        data={"application": json.dumps(_application())},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["overall_status"] == "retake"
    assert body["error_code"] == "PHOTO_QUALITY_LOW"
    assert body["error"] == RETAKE_MESSAGE
    assert body["retake"]["cause"] == "low_resolution"
    assert "20x20" not in (body["error"] or "")


def test_file_too_large_error_code(client):
    oversize = b"\x89PNG\r\n\x1a\n" + b"\x00" * (main_module.MAX_FILE_SIZE + 1)
    res = client.post(
        "/api/review",
        files=[("files", ("huge.png", oversize, "image/png"))],
        data={"application": json.dumps(_application())},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["overall_status"] == "error"
    assert body["error_code"] == "FILE_TOO_LARGE"


def test_upstream_timeout_is_typed_http_error(client):
    err = ClientError(
        "UPSTREAM_TIMEOUT",
        "The label reader timed out — your photo is fine. Try again shortly.",
    )
    with patch(
        "app.main._extract_fields_with_retry",
        AsyncMock(return_value=(None, err)),
    ):
        res = client.post(
            "/api/review",
            files=[("files", ("front.png", _sample_png(), "image/png"))],
            data={"application": json.dumps(_application())},
        )
    assert res.status_code == 504
    body = res.json()
    assert body["error_code"] == "UPSTREAM_TIMEOUT"
    assert body.get("overall_status") not in {"pass", "fail"}
    assert "claude" not in json.dumps(body).lower()
    assert "anthropic" not in json.dumps(body).lower()


def test_upstream_error_is_typed_http_error(client):
    err = ClientError(
        "UPSTREAM_ERROR",
        "The label reader is unavailable right now — your photo is fine. Try again shortly.",
    )
    with patch(
        "app.main._extract_fields_with_retry",
        AsyncMock(return_value=(None, err)),
    ):
        res = client.post(
            "/api/review",
            files=[("files", ("front.png", _sample_png(), "image/png"))],
            data={"application": json.dumps(_application())},
        )
    assert res.status_code == 502
    body = res.json()
    assert body["error_code"] == "UPSTREAM_ERROR"
    assert body.get("overall_status") not in {"pass", "fail"}
    assert "sk-ant-" not in json.dumps(body)


def test_invalid_application_http_error_has_contract(client):
    res = client.post(
        "/api/review",
        files=[("files", ("front.png", _sample_png(), "image/png"))],
        data={"application": "not-json"},
    )
    assert res.status_code == 422
    body = res.json()
    assert body["error_code"] == "INVALID_REQUEST"
    assert body["message"]
    assert body["request_id"]
    assert body["detail"]
    assert res.headers.get("x-request-id") == body["request_id"]


def test_label_check_low_confidence_is_retake(client):
    extracted = _readable_extraction(
        name_and_address="",
        beverage_type_guess="distilled_spirits",
        per_field_confidence={
            "brand_name": 0.95,
            "class_type": 0.9,
            "alcohol_content": 0.9,
            "net_contents": 0.9,
            "name_and_address": 0.0,
            "government_warning_body": 0.8,
        },
    )
    with patch(
        "app.main._extract_fields_with_retry",
        AsyncMock(return_value=(extracted, None)),
    ):
        res = client.post(
            "/api/label-check/batch",
            files=[("files", ("front.png", _sample_png(), "image/png"))],
            data={
                "image_counts": "[1]",
                "confirmed_beverage_type": "distilled_spirits",
            },
        )
    assert res.status_code == 200
    body = res.json()[0]
    assert body["overall_status"] == "fail"
    assert body["field_status"]["name_and_address"] == "absent"
    check_names = {item["field"] for item in body["checks"]}
    assert "brand_name" in check_names
    assert "name_and_address" in check_names
    addr = next(item for item in body["checks"] if item["field"] == "name_and_address")
    assert addr["status"] == "fail"
    assert "not found on the label" in addr["message"].lower()


def test_scrub_user_message_strips_secrets_and_paths():
    dirty = (
        "Claude API error (529): overloaded using claude-sonnet-4-6 "
        "sk-ant-api03-SECRETFILE key=/home/ubuntu/.secrets/key.pem\n"
        "Traceback (most recent call last):\n  File \"/app/main.py\", line 1"
    )
    cleaned = scrub_user_message(dirty)
    lowered = cleaned.lower()
    assert "sk-ant-" not in lowered
    assert "claude-sonnet" not in lowered
    assert "/home/ubuntu" not in cleaned
    assert "traceback" not in lowered
    assert ".pem" not in lowered


def test_current_request_id_roundtrip():
    rid = generate_request_id()
    set_request_id(rid)
    assert current_request_id() == rid


def test_missing_form_field_returns_invalid_request_contract(client):
    """RequestValidationError (missing files) must use the error contract, not FastAPI's default 422."""
    res = client.post(
        "/api/review",
        data={"application": json.dumps(_application())},
    )
    assert res.status_code == 422
    body = res.json()
    assert body["error_code"] == "INVALID_REQUEST"
    assert body["message"]
    assert body["request_id"]
    assert body["detail"]
    assert res.headers.get("x-request-id") == body["request_id"]
    dumped = json.dumps(body).lower()
    assert "traceback" not in dumped
    assert "loc" not in dumped or body["error_code"] == "INVALID_REQUEST"


def test_stream_non_image_is_unsupported_file_not_retake(client):
    """Renamed text must be UNSUPPORTED_FILE on the streaming route, matching /api/review."""
    res = client.post(
        "/api/review/batch/stream",
        files=[("files", ("notes.jpg", b"just some text", "image/jpeg"))],
        data={
            "image_counts": "[1]",
            "applications": json.dumps([_application()]),
        },
    )
    assert res.status_code == 200
    lines = [json.loads(line) for line in res.text.strip().split("\n") if line.strip()]
    result_line = next(item for item in lines if "result" in item)
    body = result_line["result"]
    assert body["overall_status"] == "error"
    assert body["error_code"] == "UNSUPPORTED_FILE"
    assert body["error"] != RETAKE_MESSAGE
    assert "traceback" not in (body["error"] or "").lower()
    assert res.headers.get("x-request-id")


def _assert_config_error_body(res):
    """HTTP 503 VISION_UNAVAILABLE must be structured and must not leak key material."""
    assert res.status_code == 503
    body = res.json()
    assert body["error_code"] in {"VISION_UNAVAILABLE", "CONFIG_ERROR"}
    assert "photo is fine" in body["message"].lower()
    assert body.get("overall_status") not in {"pass", "fail"}
    dumped = json.dumps(body).lower()
    assert "sk-ant-" not in dumped
    assert "anthropic" not in dumped
    assert "traceback" not in dumped
    assert "an exception occurred" not in dumped
    assert res.headers.get("x-request-id") == body["request_id"]


def _readable_png() -> bytes:
    """Large enough to pass photo pre-gate so the model-config path is reached."""
    return _sample_png(400, 400)


def test_missing_api_key_returns_503_config_error(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = client.post(
        "/api/review",
        files=[("files", ("front.png", _readable_png(), "image/png"))],
        data={"application": json.dumps(_application())},
    )
    _assert_config_error_body(res)


def test_missing_api_key_label_check_returns_503(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = client.post(
        "/api/label-check/batch",
        files=[("files", ("front.png", _readable_png(), "image/png"))],
        data={"image_counts": "[1]"},
    )
    _assert_config_error_body(res)


def test_missing_api_key_batch_returns_503(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = client.post(
        "/api/review/batch",
        files=[("files", ("front.png", _readable_png(), "image/png"))],
        data={
            "image_counts": "[1]",
            "applications": json.dumps([_application()]),
        },
    )
    _assert_config_error_body(res)


def test_missing_api_key_stream_returns_503_not_ndjson(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = client.post(
        "/api/review/batch/stream",
        files=[("files", ("front.png", _readable_png(), "image/png"))],
        data={
            "image_counts": "[1]",
            "applications": json.dumps([_application()]),
        },
    )
    _assert_config_error_body(res)
    assert "application/json" in (res.headers.get("content-type") or "")


def test_invalid_api_key_exception_returns_503(client, monkeypatch):
    """Rejected-key exceptions must be HTTP 503 VISION_UNAVAILABLE, never 200 INTERNAL_ERROR."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key-for-tests")
    with patch(
        "app.main._run_extract_sync",
        side_effect=Exception("invalid x-api-key header from provider"),
    ):
        res = client.post(
            "/api/review",
            files=[("files", ("front.png", _readable_png(), "image/png"))],
            data={"application": json.dumps(_application())},
        )
    _assert_config_error_body(res)
    assert "sk-ant-not-a-real-key" not in json.dumps(res.json()).lower()


def test_client_error_from_exception_maps_auth_without_leaking():
    from app.main import _client_error_from_exception

    mapped = _client_error_from_exception(
        Exception("Could not resolve authentication method. api_key client option must be set.")
    )
    assert mapped.code in {"VISION_UNAVAILABLE", "CONFIG_ERROR"}
    assert "sk-ant-" not in mapped.message
    assert "anthropic" not in mapped.message.lower()
    assert "photo is fine" in mapped.message.lower()
