"""Triage fixes for multi-image type-size, stream retry, address, merge index, docs CSP."""

from __future__ import annotations

import inspect
import io
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import claude_client
from app.compliance import (
    _brand_text_is_derived_from_bottler,
    _build_warning_format,
    _is_city_state_or_street_line,
    _producer_entity_name,
    merge_extracted_label_data,
)
from app.cv_typesize import CalibrationMethod
from app.main import _evaluate_type_size, app
from app.models import ExtractedLabelData, FieldLocation, TypeSizeMeasurement
from app.security import API_CSP, DOCS_CSP, SecurityHeadersMiddleware, csp_for_path


def _png(width: int = 80, height: int = 60, color=(40, 80, 120)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _extracted(**overrides) -> ExtractedLabelData:
    base = dict(
        brand_name="NORTH STAR",
        class_type="Bourbon",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General women should not drink.",
        extraction_confidence=0.95,
    )
    base.update(overrides)
    return ExtractedLabelData(**base)


# ---------------------------------------------------------------------------
# 1. Type-size boxes are evaluated against the photo they came from
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_type_size_uses_bbox_from_matching_image_index(monkeypatch: pytest.MonkeyPatch):
    seen: list[tuple[str, tuple | None]] = []

    def fake_eval(img_bytes, filename="x", net_contents=None, gw_bbox=None, warning_text=None, **_k):
        seen.append((filename, gw_bbox))
        return TypeSizeMeasurement(
            method=CalibrationMethod.UNCALIBRATED_ESTIMATE,
            required_min_height_mm=2.0,
            state="cannot_measure",
            verification_note="test",
        )

    monkeypatch.setattr("app.main.evaluate_type_size_from_image_bytes", fake_eval)
    extracted = _extracted(
        field_locations=[
            FieldLocation(
                field="government_warning",
                image_index=1,
                x=0.2,
                y=0.6,
                width=0.5,
                height=0.2,
                confidence="high",
            ),
        ]
    )
    await _evaluate_type_size(
        [(_png(100, 80), "front.png"), (_png(200, 160), "back.png")],
        extracted,
        "750 mL",
        "test",
    )
    # gather() runs photos concurrently; assert the pairing, not call order.
    assert set(seen) == {
        ("front.png", None),
        ("back.png", (0.2, 0.6, 0.5, 0.2)),
    }


# ---------------------------------------------------------------------------
# 2. Streaming batch uses the same bounded retry as the single-label path
# ---------------------------------------------------------------------------

def test_streaming_batch_retries_transient_failure_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    calls = {"n": 0}

    class _Timeout(claude_client.APITimeoutError):
        def __init__(self):
            Exception.__init__(self, "timeout")

    def flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Timeout()
        return _extracted()

    monkeypatch.setattr("app.main.extract_label_fields", flaky)
    monkeypatch.setattr("app.main.asyncio.sleep", AsyncMock())
    client = TestClient(app)
    resp = client.post(
        "/api/review/batch/stream",
        files=[("files", ("label.png", _png(150, 150), "image/png"))],
        data={
            "image_counts": "[1]",
            "applications": json.dumps([{
                "beverage_type": "distilled_spirits",
                "brand_name": "NORTH STAR",
                "class_type": "Bourbon",
                "alcohol_content": "45% Alc./Vol.",
                "net_contents": "750 mL",
            }]),
        },
    )
    assert resp.status_code == 200
    assert calls["n"] == 2
    lines = [json.loads(line) for line in resp.text.strip().split("\n") if line.strip()]
    result_lines = [item for item in lines if "result" in item]
    assert result_lines
    assert result_lines[0]["index"] == 0
    assert result_lines[0]["result"]["overall_status"] != "error"


def test_streaming_batch_fails_cleanly_per_item_after_retry_budget(monkeypatch: pytest.MonkeyPatch):
    class _Timeout(claude_client.APITimeoutError):
        def __init__(self):
            Exception.__init__(self, "timeout")

    monkeypatch.setattr(
        "app.main.extract_label_fields",
        lambda *_a, **_k: (_ for _ in ()).throw(_Timeout()),
    )
    monkeypatch.setattr("app.main.asyncio.sleep", AsyncMock())
    client = TestClient(app)
    resp = client.post(
        "/api/review/batch/stream",
        files=[("files", ("label.png", _png(150, 150), "image/png"))],
        data={
            "image_counts": "[1]",
            "applications": json.dumps([{
                "beverage_type": "distilled_spirits",
                "brand_name": "NORTH STAR",
                "class_type": "Bourbon",
                "alcohol_content": "45% Alc./Vol.",
                "net_contents": "750 mL",
            }]),
        },
    )
    assert resp.status_code == 504
    body = resp.json()
    assert body["error_code"] == "UPSTREAM_TIMEOUT"
    assert body.get("overall_status") not in {"pass", "fail", "error"}
    assert "application/json" in (resp.headers.get("content-type") or "")


# ---------------------------------------------------------------------------
# 3. City + state abbreviation is not a company name
# ---------------------------------------------------------------------------

def test_denver_co_is_city_state_not_company():
    assert _is_city_state_or_street_line("Denver CO") is True
    assert _producer_entity_name("Denver CO") == ""
    assert _producer_entity_name("Imported by Denver CO") == ""
    assert _producer_entity_name("Produced and Bottled by Crescent Ridge Winery\nDenver CO") == (
        "crescent ridge winery"
    )
    # Three-token legal suffix without ZIP is still a company.
    assert _is_city_state_or_street_line("Hopline Brewing Co") is False
    # Company + city + state stays a company clause, not a bare address.
    assert _is_city_state_or_street_line(
        "Buffalo Trace Distillery, Frankfort, KY"
    ) is False
    assert _producer_entity_name(
        "Distilled and Bottled by Buffalo Trace Distillery, Frankfort, KY"
    ) == "buffalo trace distillery"
    assert _producer_entity_name(
        "Produced and Bottled by Crescent Ridge Winery, Paso Robles, CA"
    ) == "crescent ridge winery"
    # Multi-word Colorado cities must not be kept as the bottler company.
    assert _is_city_state_or_street_line("Colorado Springs CO") is True
    assert _is_city_state_or_street_line("Fort Collins CO") is True
    assert _is_city_state_or_street_line("Colorado Springs CO 80903") is True
    assert _producer_entity_name("Imported by Colorado Springs CO") == ""
    assert _producer_entity_name(
        "Produced and Bottled by Crescent Ridge Winery\nFort Collins CO"
    ) == "crescent ridge winery"
    assert _brand_text_is_derived_from_bottler(
        "COLORADO SPRINGS CO", "Imported by Colorado Springs CO"
    ) is False
    # Industry word before Co still marks a company, including Fort+Co towns.
    assert _is_city_state_or_street_line("Hopline Brewing Co") is False
    assert _is_city_state_or_street_line("Fort Collins Brewing Co") is False
    assert _is_city_state_or_street_line("Colorado Gold Co") is False
    assert _producer_entity_name(
        "Brewed and Bottled by Fort Collins Brewing Co, Fort Collins, CO"
    ) == "fort collins brewing co"
    assert _producer_entity_name(
        "Produced and Bottled by Colorado Gold Co"
    ) == "colorado gold co"
    # Street + city + state is an address, not a company named after the street.
    assert _is_city_state_or_street_line("Main Street, Louisville, KY") is True
    assert _is_city_state_or_street_line("PO Box 123, Denver, CO") is True
    assert _is_city_state_or_street_line("Rural Route 2, Fort Collins, CO") is True
    assert _producer_entity_name("Main Street, Louisville, KY") == ""
    assert _producer_entity_name(
        "Produced and Bottled by Crescent Ridge Winery\nMain Street, Paso Robles, CA"
    ) == "crescent ridge winery"


def test_brand_denver_co_is_not_derived_from_address_city_state():
    assert _brand_text_is_derived_from_bottler("Denver CO", "Imported by Denver CO") is False
    assert _brand_text_is_derived_from_bottler("BLUE HARBOR", "Imported by Denver CO") is False
    assert _producer_entity_name("Imported by Denver CO") == ""


# ---------------------------------------------------------------------------
# 4. Merged per-photo boxes stay on the photo they came from
# ---------------------------------------------------------------------------

def test_merge_remaps_field_location_image_index_to_source_photo():
    front = _extracted(
        field_locations=[
            FieldLocation(field="brand_name", image_index=0, x=0.1, y=0.1, width=0.4, height=0.1, confidence="high"),
        ]
    )
    back = _extracted(
        field_locations=[
            FieldLocation(
                field="government_warning",
                image_index=0,
                x=0.2,
                y=0.7,
                width=0.6,
                height=0.2,
                confidence="high",
            ),
        ]
    )
    merged = merge_extracted_label_data([front, back], photo_roles=["front", "back"])
    by_field = {loc.field: loc.image_index for loc in merged.field_locations}
    assert by_field["brand_name"] == 0
    assert by_field["government_warning"] == 1


# ---------------------------------------------------------------------------
# 8. Docs CSP is tighter than a wildcard but still loads Swagger / ReDoc
# ---------------------------------------------------------------------------

def test_docs_csp_is_not_the_locked_api_policy():
    assert csp_for_path("/docs") == DOCS_CSP
    assert csp_for_path("/redoc") == DOCS_CSP
    assert csp_for_path("/openapi.json") == DOCS_CSP
    assert csp_for_path("/docs/oauth2-redirect") == DOCS_CSP
    assert csp_for_path("/api/health") == API_CSP
    assert csp_for_path("/api/review") == API_CSP
    assert "default-src 'none'" in DOCS_CSP
    assert "https://cdn.jsdelivr.net" in DOCS_CSP
    assert "connect-src 'self'" in DOCS_CSP
    assert "*" not in DOCS_CSP.replace("'self'", "")


def test_docs_route_receives_docs_csp_not_api_csp():
    docs_app = FastAPI(title="docs-csp")
    docs_app.add_middleware(SecurityHeadersMiddleware)
    client = TestClient(docs_app)
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert resp.headers.get("content-security-policy") == DOCS_CSP
    assert "swagger" in resp.text.lower() or "openapi" in resp.text.lower()


# ---------------------------------------------------------------------------
# 9. Bold-heading field description matches the hard-fail check
# ---------------------------------------------------------------------------

def test_bold_heading_models_doc_matches_hard_fail():
    field = ExtractedLabelData.model_fields["government_warning_header_appears_bold"]
    description = field.description or ""
    assert "hard fail" in description.lower()
    assert "advisory" in description.lower()
    source = inspect.getsource(_build_warning_format)
    assert "government_warning_header_appears_bold is False" in source
    assert 'status="fail"' in source or "status='fail'" in source
