"""Regression tests for PR #26 review-thread fixes.

Covers type-size image-index filtering, streamed request-size caps, CORS on
413s, docs CSP, stream Claude retry, and wine-fixture rendering. The demo
access-token gate stays wired to review endpoints.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.compliance import _producer_entity_name, merge_extracted_label_data
from app.main import _evaluate_type_size, _extract_fields_with_retry, app
from app.models import CalibrationMethod, ExtractedLabelData, FieldLocation, TypeSizeMeasurement
from app.request_limits import RequestBodyLimitMiddleware
from app.security import API_CSP, DOCS_CSP, csp_for_path

FIXTURE_DIR = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(FIXTURE_DIR))

from synthetic_scenario_matrix import BASE_WINE, render_label  # noqa: E402


def _png(width: int = 80, height: int = 80) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (90, 110, 130)).save(buf, format="PNG")
    return buf.getvalue()


VALID_APPLICATION = json.dumps({
    "beverage_type": "distilled_spirits",
    "brand_name": "Old Tom Distillery",
    "class_type": "Bourbon",
    "alcohol_content": "45% ABV",
    "net_contents": "750 mL",
})


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_review_endpoints_still_declare_access_token_gate():
    """The demo access-token dependency must remain on every review route."""
    gated = {
        route.path
        for route in app.routes
        if getattr(route, "dependant", None)
        and any(
            getattr(dep.call, "__name__", "") == "require_demo_access"
            for dep in route.dependant.dependencies
        )
    }
    assert "/api/review" in gated
    assert "/api/review/batch" in gated
    assert "/api/review/batch/stream" in gated
    assert "/api/label-check/batch" in gated
    assert "/api/cola/parse-template" in gated
    assert "/api/health" not in gated


def test_government_warning_bbox_is_scoped_to_image_index():
    """A warning box on photo 1 must never be reused on photo 0."""
    from app.main import _government_warning_bbox_for_image

    extracted = ExtractedLabelData(
        field_locations=[
            FieldLocation(
                field="government_warning",
                image_index=1,
                x=0.1,
                y=0.2,
                width=0.3,
                height=0.4,
            ),
        ],
    )
    assert _government_warning_bbox_for_image(extracted, 0) is None
    assert _government_warning_bbox_for_image(extracted, 1) == (0.1, 0.2, 0.3, 0.4)


@pytest.mark.asyncio
async def test_evaluate_type_size_uses_matching_image_index_only():
    # Multi-photo measurement runs concurrently in a threadpool, so call order
    # is not stable. Key the captured bbox by filename instead.
    captured: dict[str, tuple | None] = {}

    def fake_eval(*_args, **kwargs):
        captured[kwargs["filename"]] = kwargs.get("gw_bbox")
        return TypeSizeMeasurement(
            method=CalibrationMethod.UNCALIBRATED_ESTIMATE,
            required_min_height_mm=2.0,
            state="cannot_measure",
            verification_note="test",
        )

    extracted = ExtractedLabelData(
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General...",
        field_locations=[
            FieldLocation(
                field="government_warning",
                image_index=1,
                x=0.1,
                y=0.2,
                width=0.3,
                height=0.4,
            ),
        ],
    )
    with patch("app.main.evaluate_type_size_from_image_bytes", side_effect=fake_eval):
        await _evaluate_type_size(
            [(b"front-bytes", "front.jpg"), (b"back-bytes", "back.jpg")],
            extracted,
            "750 mL",
            "test",
        )
    assert captured == {
        "front.jpg": None,
        "back.jpg": (0.1, 0.2, 0.3, 0.4),
    }


def test_merge_remaps_field_location_image_index():
    front = ExtractedLabelData(
        field_locations=[
            FieldLocation(field="brand_name", image_index=0, x=0.1, y=0.1, width=0.2, height=0.1),
        ]
    )
    back = ExtractedLabelData(
        field_locations=[
            FieldLocation(
                field="government_warning",
                image_index=0,
                x=0.2,
                y=0.5,
                width=0.6,
                height=0.2,
            ),
        ]
    )
    merged = merge_extracted_label_data([front, back], photo_roles=["front", "back"])
    by_field = {loc.field: loc.image_index for loc in merged.field_locations}
    assert by_field["brand_name"] == 0
    assert by_field["government_warning"] == 1


def test_denver_co_without_zip_is_not_folded_into_company_name():
    name = _producer_entity_name(
        "Produced and Bottled by Crescent Ridge Winery\nDenver CO"
    )
    assert name == "crescent ridge winery"
    assert "denver" not in name


def test_brewing_co_is_still_the_company_name():
    name = _producer_entity_name("Brewed and Canned by Hopline Brewing Co., Portland, OR")
    assert "hopline brewing co" in name


@pytest.mark.asyncio
async def test_chunked_body_without_content_length_is_capped(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "50")
    inner_reads = {"n": 0}

    async def inner(scope, receive, send):
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                break
            inner_reads["n"] += 1
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RequestBodyLimitMiddleware(inner)
    chunks = [b"a" * 40, b"b" * 40]

    async def receive():
        if chunks:
            body = chunks.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await middleware(
        {"type": "http", "path": "/api/review", "headers": []},
        receive,
        send,
    )
    starts = [item for item in sent if item["type"] == "http.response.start"]
    assert starts
    assert starts[0]["status"] == 413
    bodies = [item for item in sent if item["type"] == "http.response.body"]
    assert any(b"FILE_TOO_LARGE" in (item.get("body") or b"") for item in bodies)


def test_oversize_413_includes_cors_for_allowed_origin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "200")
    origin = "https://ttb-label-frontend.onrender.com"
    resp = client.post(
        "/api/review",
        files=[("files", ("front.png", _png(), "image/png"))],
        data={"application": VALID_APPLICATION},
        headers={"Origin": origin},
    )
    assert resp.status_code == 413
    assert resp.json()["error_code"] == "FILE_TOO_LARGE"
    assert resp.headers.get("access-control-allow-origin") == origin
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_docs_csp_is_separate_from_api_csp(client: TestClient):
    assert csp_for_path("/api/review") == API_CSP
    assert csp_for_path("/api/health") == API_CSP
    docs = csp_for_path("/docs")
    redoc = csp_for_path("/redoc")
    assert docs == DOCS_CSP
    assert redoc == DOCS_CSP
    assert "cdn.jsdelivr.net" in docs
    assert docs != API_CSP
    # Path-based CSP applies even when /docs is disabled (404).
    resp = client.get("/docs")
    assert resp.headers.get("content-security-policy") == DOCS_CSP
    assert client.get("/api/health").headers.get("content-security-policy") == API_CSP


@pytest.mark.asyncio
async def test_stream_path_uses_extract_fields_with_retry():
    source = __import__("inspect").getsource(
        __import__("app.main", fromlist=["review_labels_batch_stream"]).review_labels_batch_stream
    )
    assert "_extract_fields_with_retry" in source
    assert "_run_extract_sync" not in source or source.count("_extract_fields_with_retry") >= 1


@pytest.mark.asyncio
async def test_stream_retries_transient_timeout(monkeypatch: pytest.MonkeyPatch):
    from app import claude_client

    calls = {"n": 0}

    class _Timeout(claude_client.APITimeoutError):
        def __init__(self):
            Exception.__init__(self, "timeout")

    def flaky(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Timeout()
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

    monkeypatch.setattr("app.main.extract_label_fields", flaky)
    monkeypatch.setattr("app.main.asyncio.sleep", AsyncMock())
    extracted, error = await _extract_fields_with_retry([(_png(), "label.png")])
    assert error is None
    assert extracted is not None
    assert calls["n"] == 2


def test_wine_imported_pass_renders_origin_and_sulfites():
    with_fields = render_label(BASE_WINE, theme="wine")
    without = render_label(
        {
            key: value
            for key, value in BASE_WINE.items()
            if key not in ("country_of_origin", "sulfite_declaration")
        },
        theme="wine",
    )
    assert with_fields.tobytes() != without.tobytes()
