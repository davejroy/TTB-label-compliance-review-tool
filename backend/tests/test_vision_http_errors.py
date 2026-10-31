"""Vision/upstream failures must leave as typed non-2xx, never pass/fail/prefill.

Mocks Anthropic SDK exceptions (no live key). Covers:
- upstream 5xx / overloaded (APIStatusError 500 / 529)
- invalid or revoked key (401 / AuthenticationError)
- timeout and connection errors
- rate limit (429)

Each case is asserted on /api/review, /api/label-check/batch,
/api/review/batch, and /api/review/batch/stream.
"""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

import app.main as main_module
from app.errors import (
    RATE_LIMITED,
    RATE_LIMITED_UPSTREAM_MESSAGE,
    UPSTREAM_ERROR,
    UPSTREAM_ERROR_MESSAGE,
    UPSTREAM_TIMEOUT,
    UPSTREAM_TIMEOUT_MESSAGE,
    VISION_UNAVAILABLE,
    VISION_UNAVAILABLE_MESSAGE,
)
from app.models import ExtractedLabelData


def _png(width: int = 400, height: int = 400) -> bytes:
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


class _StatusError(APIStatusError):
    def __init__(self, status_code: int, message: str):
        Exception.__init__(self, message)
        self.status_code = status_code
        self.body = {"type": "error", "error": {"type": "api_error", "message": message}}
        self.response = type("R", (), {"status_code": status_code, "headers": {}})()


class _AuthError(AuthenticationError):
    def __init__(self):
        Exception.__init__(self, "invalid x-api-key header (revoked)")
        self.status_code = 401
        self.body = {"type": "error", "error": {"type": "authentication_error"}}
        self.response = type("R", (), {"status_code": 401, "headers": {}})()


class _TimeoutError(APITimeoutError):
    def __init__(self):
        Exception.__init__(self, "request timed out")


class _ConnectionError(APIConnectionError):
    def __init__(self):
        Exception.__init__(self, "connection reset by peer")


class _RateError(RateLimitError):
    def __init__(self):
        Exception.__init__(self, "rate limited")
        self.status_code = 429
        self.body = {"type": "error", "error": {"type": "rate_limit_error"}}
        self.response = type("R", (), {"status_code": 429, "headers": {}})()


# (id, factory, http status, error_code, message fragment)
VISION_CASES = [
    (
        "upstream_500",
        lambda: _StatusError(500, "internal server error"),
        502,
        UPSTREAM_ERROR,
        "photo is fine",
    ),
    (
        "overloaded_529",
        lambda: _StatusError(529, "overloaded"),
        429,
        RATE_LIMITED,
        "photo is fine",
    ),
    (
        "invalid_key_401",
        lambda: _AuthError(),
        503,
        VISION_UNAVAILABLE,
        "photo is fine",
    ),
    (
        "timeout",
        lambda: _TimeoutError(),
        504,
        UPSTREAM_TIMEOUT,
        "photo is fine",
    ),
    (
        "connection",
        lambda: _ConnectionError(),
        502,
        UPSTREAM_ERROR,
        "photo is fine",
    ),
    (
        "rate_limit_429",
        lambda: _RateError(),
        429,
        RATE_LIMITED,
        "photo is fine",
    ),
]


def _assert_typed_http_error(res, status: int, code: str) -> dict:
    assert res.status_code == status
    assert res.status_code != 200
    body = res.json()
    assert body["error_code"] == code
    assert body["request_id"]
    assert res.headers.get("x-request-id") == body["request_id"]
    text = json.dumps(body).lower()
    assert "traceback" not in text
    assert "sk-ant-" not in text
    assert "anthropic" not in text
    # Must never look like a finished review.
    assert body.get("overall_status") not in {"pass", "fail", "warning", "retake"}
    assert "extracted" not in body or not body.get("extracted")
    assert "fields" not in body or body.get("fields") in (None, [])
    return body


def _post_review(client: TestClient):
    return client.post(
        "/api/review",
        files=[("files", ("front.png", _png(), "image/png"))],
        data={"application": json.dumps(_application())},
    )


def _post_label_check(client: TestClient):
    return client.post(
        "/api/label-check/batch",
        files=[("files", ("front.png", _png(), "image/png"))],
        data={"image_counts": "[1]"},
    )


def _post_batch(client: TestClient):
    return client.post(
        "/api/review/batch",
        files=[("files", ("front.png", _png(), "image/png"))],
        data={
            "image_counts": "[1]",
            "applications": json.dumps([_application()]),
        },
    )


def _post_stream(client: TestClient):
    return client.post(
        "/api/review/batch/stream",
        files=[("files", ("front.png", _png(), "image/png"))],
        data={
            "image_counts": "[1]",
            "applications": json.dumps([_application()]),
        },
    )


ROUTES = [
    ("review", _post_review),
    ("label_check", _post_label_check),
    ("batch", _post_batch),
    ("stream", _post_stream),
]


@pytest.fixture
def client():
    return TestClient(main_module.app)


@pytest.mark.parametrize("case_id,factory,status,code,fragment", VISION_CASES)
@pytest.mark.parametrize("route_id,poster", ROUTES)
def test_vision_exception_is_typed_non_2xx(
    client, monkeypatch, case_id, factory, status, code, fragment, route_id, poster
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    monkeypatch.setattr(main_module, "extract_label_fields", lambda *_a, **_k: (_ for _ in ()).throw(factory()))
    monkeypatch.setattr("app.main.asyncio.sleep", AsyncMock())

    res = poster(client)
    body = _assert_typed_http_error(res, status, code)
    assert fragment in (body.get("message") or "").lower()
    if route_id == "stream":
        assert "application/json" in (res.headers.get("content-type") or "")
        assert "ndjson" not in (res.headers.get("content-type") or "")


def test_client_error_from_exception_maps_each_class():
    from app.main import _client_error_from_exception

    mapped_500 = _client_error_from_exception(_StatusError(500, "boom"))
    assert mapped_500.code == UPSTREAM_ERROR
    assert mapped_500.message == UPSTREAM_ERROR_MESSAGE

    mapped_529 = _client_error_from_exception(_StatusError(529, "overloaded"))
    assert mapped_529.code == RATE_LIMITED
    assert mapped_529.message == RATE_LIMITED_UPSTREAM_MESSAGE

    mapped_401 = _client_error_from_exception(_AuthError())
    assert mapped_401.code == VISION_UNAVAILABLE
    assert mapped_401.message == VISION_UNAVAILABLE_MESSAGE

    mapped_timeout = _client_error_from_exception(_TimeoutError())
    assert mapped_timeout.code == UPSTREAM_TIMEOUT
    assert mapped_timeout.message == UPSTREAM_TIMEOUT_MESSAGE

    mapped_conn = _client_error_from_exception(_ConnectionError())
    assert mapped_conn.code == UPSTREAM_ERROR
    assert mapped_conn.message == UPSTREAM_ERROR_MESSAGE

    mapped_429 = _client_error_from_exception(_RateError())
    assert mapped_429.code == RATE_LIMITED
    assert mapped_429.message == RATE_LIMITED_UPSTREAM_MESSAGE


def test_health_reports_vision_configured_boolean_only(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["vision_configured"] is False
    dumped = json.dumps(body).lower()
    assert "sk-ant-" not in dumped
    assert "anthropic" not in dumped
    assert set(body.keys()) == {"status", "vision_configured"}

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-present-for-health")
    res = client.get("/api/health")
    assert res.json() == {"status": "ok", "vision_configured": True}
    assert "sk-ant-present" not in json.dumps(res.json())


def test_later_stream_vision_error_keeps_ndjson_and_done(client, monkeypatch):
    """After label 0 commits HTTP 200, a later timeout must not abort the stream."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    extracted = ExtractedLabelData(
        brand_name="BOURBON CO",
        class_type="Whiskey",
        alcohol_content="45% ABV",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General women should not drink.",
        extraction_confidence=0.95,
    )

    def extract(images, *_a, **_k):
        name = images[0][1] if images else ""
        if "first.png" in name:
            return extracted
        raise _TimeoutError()

    monkeypatch.setattr(main_module, "extract_label_fields", extract)
    monkeypatch.setattr("app.main.asyncio.sleep", AsyncMock())

    res = client.post(
        "/api/review/batch/stream",
        files=[
            ("files", ("first.png", _png(), "image/png")),
            ("files", ("second.png", _png(), "image/png")),
        ],
        data={
            "image_counts": "[1,1]",
            "applications": json.dumps([_application(), _application()]),
        },
    )
    assert res.status_code == 200
    assert "ndjson" in (res.headers.get("content-type") or "")
    lines = [json.loads(line) for line in res.text.strip().split("\n") if line.strip()]
    by_index = {item["index"]: item["result"] for item in lines if "result" in item}
    assert set(by_index) == {0, 1}
    assert by_index[0]["overall_status"] != "error"
    assert by_index[1]["overall_status"] == "error"
    assert by_index[1]["error_code"] == UPSTREAM_TIMEOUT
    assert by_index[1]["overall_status"] not in {"pass", "fail"}
    assert any(item.get("done") is True for item in lines)
    dumped = res.text.lower()
    assert "traceback" not in dumped
    assert "sk-ant-" not in dumped


def test_later_stream_structured_http_keeps_index_and_done(client, monkeypatch):
    """as_completed wrappers must not drop a later-label StructuredHTTPException row."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    extracted = ExtractedLabelData(
        brand_name="BOURBON CO",
        class_type="Whiskey",
        alcohol_content="45% ABV",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General women should not drink.",
        extraction_confidence=0.95,
    )

    async def type_size(images, *_a, **_k):
        name = images[0][1] if images else ""
        if "second.png" in name:
            raise main_module.StructuredHTTPException(
                502, UPSTREAM_ERROR, UPSTREAM_ERROR_MESSAGE
            )
        return None

    monkeypatch.setattr(main_module, "extract_label_fields", lambda *_a, **_k: extracted)
    monkeypatch.setattr(main_module, "_evaluate_type_size", type_size)

    res = client.post(
        "/api/review/batch/stream",
        files=[
            ("files", ("first.png", _png(), "image/png")),
            ("files", ("second.png", _png(), "image/png")),
        ],
        data={
            "image_counts": "[1,1]",
            "applications": json.dumps([_application(), _application()]),
        },
    )
    assert res.status_code == 200
    assert "ndjson" in (res.headers.get("content-type") or "")
    lines = [json.loads(line) for line in res.text.strip().split("\n") if line.strip()]
    by_index = {item["index"]: item["result"] for item in lines if "result" in item}
    assert set(by_index) == {0, 1}
    assert by_index[0]["overall_status"] != "error"
    assert by_index[1]["overall_status"] == "error"
    assert by_index[1]["error_code"] == UPSTREAM_ERROR
    assert any(item.get("done") is True for item in lines)
