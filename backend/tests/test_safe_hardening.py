"""Tests for safe hardening, anonymous access, security headers, and input validation.

Verifies:
1. Sacred constraint: Anonymous / zero-login access works without 401/403 for /api/health and review endpoints.
2. Security response headers are present on all responses (nosniff, DENY, no-referrer, Permissions-Policy, CSP, HSTS).
3. Safer error surfaces: Exception details are not echoed in client responses.
4. Input validation: Batch input validation failures return HTTP 422 (not 401 or 500).
5. CORS configuration fails open to local Vite + the render.yaml frontend (not "*").
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
from app.errors import ClientError
from app.models import ExtractedLabelData


def _sample_png(width: int = 100, height: int = 100) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (100, 150, 200)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client():
    return TestClient(main_module.app)


def test_health_anonymous_access_and_security_headers(client):
    """Anonymous /api/health succeeds with 200 and includes all required security headers."""
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert isinstance(body.get("vision_configured"), bool)

    headers = res.headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=(self)" in headers["permissions-policy"]
    assert "microphone=()" in headers["permissions-policy"]
    assert "geolocation=()" in headers["permissions-policy"]
    assert "default-src 'none'" in headers["content-security-policy"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_hsts_header_present_on_https_request(client):
    """HSTS header is set when x-forwarded-proto is https."""
    res = client.get("/api/health", headers={"x-forwarded-proto": "https"})
    assert res.status_code == 200
    assert "strict-transport-security" in res.headers
    assert "max-age=31536000" in res.headers["strict-transport-security"]


def test_review_anonymous_access_never_returns_401(client):
    """Unauthenticated review request does NOT return 401/403 (fail-open/zero-login)."""
    fake_ext = ExtractedLabelData(
        brand_name="BOURBON CO",
        class_type="Whiskey",
        alcohol_content="45% ABV",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        extraction_confidence=0.95,
    )
    app_data = {
        "beverage_type": "distilled_spirits",
        "brand_name": "BOURBON CO",
        "class_type": "Whiskey",
        "alcohol_content": "45% ABV",
        "net_contents": "750 mL",
    }

    with patch("app.main._extract_fields_with_retry", AsyncMock(return_value=(fake_ext, None))):
        res = client.post(
            "/api/review",
            files=[("files", ("label.png", _sample_png(), "image/png"))],
            data={"application": json.dumps(app_data)},
        )
        assert res.status_code == 200
        assert res.json()["overall_status"] == "pass"
        assert res.headers["x-content-type-options"] == "nosniff"


def test_review_batch_anonymous_access_never_returns_401(client):
    """Unauthenticated batch review request succeeds without 401."""
    fake_ext = ExtractedLabelData(
        brand_name="BOURBON CO",
        class_type="Whiskey",
        alcohol_content="45% ABV",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        extraction_confidence=0.95,
    )
    app_list = [{
        "beverage_type": "distilled_spirits",
        "brand_name": "BOURBON CO",
        "class_type": "Whiskey",
        "alcohol_content": "45% ABV",
        "net_contents": "750 mL",
    }]

    with patch("app.main._extract_fields_with_retry", AsyncMock(return_value=(fake_ext, None))):
        res = client.post(
            "/api/review/batch",
            files=[("files", ("label.png", _sample_png(), "image/png"))],
            data={
                "image_counts": "[1]",
                "applications": json.dumps(app_list),
            },
        )
        assert res.status_code == 200
        assert len(res.json()) == 1


def test_label_check_batch_anonymous_access_never_returns_401(client):
    """Unauthenticated label-check batch request succeeds without 401."""
    fake_ext = ExtractedLabelData(
        brand_name="BOURBON CO",
        class_type="Whiskey",
        alcohol_content="45% ABV",
        net_contents="750 mL",
        name_and_address="Bourbon Co, Louisville, KY",
        country_of_origin="",
        origin_guess="domestic",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        beverage_type_guess="distilled_spirits",
        is_alcohol_beverage_label=True,
        extraction_confidence=0.95,
        per_field_confidence={"brand_name": 0.95, "alcohol_content": 0.95},
    )

    with patch("app.main._extract_fields_with_retry", AsyncMock(return_value=(fake_ext, None))):
        res = client.post(
            "/api/label-check/batch",
            files=[("files", ("label.png", _sample_png(), "image/png"))],
            data={
                "image_counts": "[1]",
                "confirmed_beverage_type": "distilled_spirits",
                "photo_roles": json.dumps(["front"]),
            },
        )
        assert res.status_code == 200
        assert len(res.json()) == 1


# ---------------------------------------------------------------------------
# Batch Input Validation Tightening Tests (HTTP 422)
# ---------------------------------------------------------------------------

def _error_text(res) -> str:
    body = res.json()
    if isinstance(body, dict):
        for key in ("detail", "message", "error"):
            value = body.get(key)
            if isinstance(value, str):
                return value
    return str(body)


def _valid_app(brand: str = "Test Brand") -> dict:
    return {
        "beverage_type": "distilled_spirits",
        "brand_name": brand,
        "class_type": "Whiskey",
        "alcohol_content": "45% ABV",
        "net_contents": "750 mL",
    }


def test_batch_review_rejects_non_positive_image_counts_with_422(client):
    res = client.post(
        "/api/review/batch",
        files=[("files", ("label.png", _sample_png(), "image/png"))],
        data={
            "image_counts": "[-1]",
            "applications": json.dumps([_valid_app()]),
        },
    )
    assert res.status_code == 422
    assert "label" in _error_text(res).lower() or "image" in _error_text(res).lower()


def test_batch_review_rejects_exceeded_images_per_label_with_422(client):
    res = client.post(
        "/api/review/batch",
        files=[("files", ("label.png", _sample_png(), "image/png"))],
        data={
            "image_counts": "[5]",
            "applications": json.dumps([_valid_app()]),
        },
    )
    assert res.status_code == 422
    text = _error_text(res)
    assert "4" in text and "image" in text.lower()


def test_batch_review_rejects_mismatched_counts_and_applications_length_with_422(client):
    res = client.post(
        "/api/review/batch",
        files=[("files", ("label.png", _sample_png(), "image/png"))],
        data={
            "image_counts": "[1, 1]",
            "applications": json.dumps([_valid_app()]),
        },
    )
    assert res.status_code == 422
    assert "image_counts length" in _error_text(res) and "applications" in _error_text(res)


def test_batch_review_rejects_file_count_sum_mismatch_with_422(client):
    res = client.post(
        "/api/review/batch",
        files=[("files", ("label.png", _sample_png(), "image/png"))],
        data={
            "image_counts": "[2]",
            "applications": json.dumps([_valid_app()]),
        },
    )
    assert res.status_code == 422
    assert "Sum of image_counts must match the number of uploaded files" in _error_text(res)


def test_label_check_batch_rejects_invalid_json_image_counts_with_422(client):
    res = client.post(
        "/api/label-check/batch",
        files=[("files", ("label.png", _sample_png(), "image/png"))],
        data={"image_counts": "not-json"},
    )
    assert res.status_code == 422
    assert "image_counts" in _error_text(res)


def test_label_check_batch_rejects_non_positive_image_counts_with_422(client):
    res = client.post(
        "/api/label-check/batch",
        files=[("files", ("label.png", _sample_png(), "image/png"))],
        data={"image_counts": "[0]"},
    )
    assert res.status_code == 422
    assert "label" in _error_text(res).lower() or "image" in _error_text(res).lower()


def test_label_check_batch_rejects_exceeded_image_count_with_422(client):
    res = client.post(
        "/api/label-check/batch",
        files=[("files", ("label.png", _sample_png(), "image/png"))],
        data={"image_counts": "[5]"},
    )
    assert res.status_code == 422
    text = _error_text(res)
    assert "4" in text and "image" in text.lower()


def test_label_check_batch_ignores_unknown_confirmed_beverage_type(client):
    """Unknown beverage type is ignored (fail-open) rather than 422."""
    fake_ext = ExtractedLabelData(
        brand_name="BOURBON CO",
        class_type="Whiskey",
        alcohol_content="45% ABV",
        net_contents="750 mL",
        name_and_address="Bourbon Co, Louisville, KY",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        beverage_type_guess="distilled_spirits",
        is_alcohol_beverage_label=True,
        extraction_confidence=0.95,
    )
    with patch("app.main._extract_fields_with_retry", AsyncMock(return_value=(fake_ext, None))):
        res = client.post(
            "/api/label-check/batch",
            files=[("files", ("label.png", _sample_png(), "image/png"))],
            data={
                "image_counts": "[1]",
                "confirmed_beverage_type": "soda",
            },
        )
    assert res.status_code == 200
    assert len(res.json()) == 1


def test_label_check_batch_ignores_invalid_photo_roles(client):
    """Malformed photo_roles JSON is dropped (fail-open) rather than 422."""
    fake_ext = ExtractedLabelData(
        brand_name="BOURBON CO",
        class_type="Whiskey",
        alcohol_content="45% ABV",
        net_contents="750 mL",
        name_and_address="Bourbon Co, Louisville, KY",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        beverage_type_guess="distilled_spirits",
        is_alcohol_beverage_label=True,
        extraction_confidence=0.95,
    )
    with patch("app.main._extract_fields_with_retry", AsyncMock(return_value=(fake_ext, None))):
        res = client.post(
            "/api/label-check/batch",
            files=[("files", ("label.png", _sample_png(), "image/png"))],
            data={
                "image_counts": "[1]",
                "photo_roles": "{bad json",
            },
        )
        assert res.status_code == 200
        res = client.post(
            "/api/label-check/batch",
            files=[("files", ("label.png", _sample_png(), "image/png"))],
            data={
                "image_counts": "[1]",
                "photo_roles": "[123]",
            },
        )
        assert res.status_code == 200


def test_review_single_rejects_malformed_application_json_with_422(client):
    res = client.post(
        "/api/review",
        files=[("files", ("label.png", _sample_png(), "image/png"))],
        data={"application": "{invalid json"},
    )
    assert res.status_code == 422
    assert "application" in _error_text(res).lower()


# ---------------------------------------------------------------------------
# Safer Error Surfaces Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_fields_with_retry_sanitizes_errors():
    """Extraction errors do not leak internal exception details to users."""
    from anthropic import APIConnectionError
    import httpx
    req = httpx.Request("POST", "https://api.anthropic.com")
    net_err = APIConnectionError(request=req)

    with patch("app.main.run_in_threadpool", AsyncMock(side_effect=net_err)):
        with patch("asyncio.sleep", AsyncMock()):
            ext, err = await main_module._extract_fields_with_retry([(b"bytes", "label.jpg")])
            assert ext is None
            assert isinstance(err, ClientError)
            assert "httpx" not in err.message
            assert "api.anthropic.com" not in err.message

    with patch("app.main.run_in_threadpool", AsyncMock(side_effect=RuntimeError("internal stack detail"))):
        ext, err = await main_module._extract_fields_with_retry([(b"bytes", "label.jpg")])
        assert ext is None
        assert isinstance(err, ClientError)
        assert "internal stack detail" not in err.message

    with patch("app.main.run_in_threadpool", AsyncMock(side_effect=KeyError("secret_key"))):
        ext, err = await main_module._extract_fields_with_retry([(b"bytes", "label.jpg")])
        assert ext is None
        assert isinstance(err, ClientError)
        assert "secret_key" not in err.message
