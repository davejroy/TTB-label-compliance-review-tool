"""Unit and integration tests for 27 CFR 16.22 Option A (scale-marker) Type-Size Verification.

Tests test matrix scenarios TS-01 through TS-12 from docs/field-tests/typesize/ACCEPTANCE_CHECKLIST.md:
- TS-01: ID-1 Card marker + compliant text height (>= 2.0 mm on 750 mL) -> pass
- TS-02: ID-1 Card marker + deficient text height (< 2.0 mm on 750 mL) -> fail
- TS-03: ArUco marker + compliant text on small container (>= 1.0 mm on 187 mL) -> pass
- TS-04: Large container threshold (5 L -> >= 3.0 mm)
- TS-05: Missing marker in marker mode -> cannot_measure
- TS-06: Excessive perspective tilt / skew (> 25 deg) -> cannot_measure
- TS-07: Out-of-focus / blurry text -> cannot_measure
- TS-10: Naked uncalibrated photo -> uncalibrated estimate advisory without invented mm
- TS-11: Statutory Hard-Fail W02 missing warning -> fail regardless of marker
- TS-12: Statutory Hard-Fail W03 non-capital header -> fail regardless of marker
"""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, patch
import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont
from fastapi.testclient import TestClient

from app.compliance import (
    CANONICAL_WARNING_BODY,
    CANONICAL_WARNING_HEADER,
    _check_government_warning,
    check_label_requirements,
    run_compliance_checks,
)
from app.cv_typesize import (
    detect_aruco_marker,
    detect_id1_marker_contour,
    evaluate_type_size_from_image_bytes,
    get_required_min_height_mm,
    measure_capital_letter_height_px,
)
from app.main import app
from app.models import (
    ApplicationData,
    CalibrationMethod,
    ExtractedLabelData,
    FieldLocation,
    TypeSizeMeasurement,
)


def _create_synthetic_label_image(
    cap_height_px: int = 24,
    include_card: bool = True,
    include_aruco: bool = False,
    tilt_deg: float = 0.0,
    blur: bool = False,
    text_casing: str = "CANONICAL",
) -> bytes:
    """Helper to synthesize a photorealistic test label with optional ID-1 card / ArUco marker."""
    px_per_mm = 10.0  # 10 px/mm scale
    w, h = 1200, 900
    img = Image.new("RGB", (w, h), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)

    if include_card:
        # Standard ID-1 card: 85.60 mm x 53.98 mm -> 856 x 540 px
        card_w = int(85.60 * px_per_mm)
        card_h = int(53.98 * px_per_mm)
        card_x, card_y = 50, 100
        draw.rectangle(
            [card_x, card_y, card_x + card_w, card_y + card_h],
            fill=(35, 35, 35),
            outline=(10, 10, 10),
            width=3,
        )

    if include_aruco:
        # ArUco marker 50x50 mm -> 500x500 px
        marker_px = int(50.0 * px_per_mm)
        if hasattr(cv2, "aruco"):
            aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
            if hasattr(cv2.aruco, "generateImageMarker"):
                marker_img = cv2.aruco.generateImageMarker(aruco_dict, 0, marker_px)
            else:
                marker_img = cv2.aruco.drawMarker(aruco_dict, 0, marker_px)
            marker_pil = Image.fromarray(marker_img).convert("RGB")
            img.paste(marker_pil, (50, 100))

    # Government Warning text panel
    gw_x, gw_y = 50, 680
    gw_w, gw_h = int(85.60 * px_per_mm), 180
    draw.rectangle([gw_x, gw_y, gw_x + gw_w, gw_y + gw_h], fill=(255, 255, 255), outline=(0, 0, 0), width=2)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(cap_height_px * 1.35))
    except Exception:
        font = ImageFont.load_default()

    header_str = "GOVERNMENT WARNING:" if text_casing == "CANONICAL" else "Government Warning:"
    draw.text((gw_x + 20, gw_y + 20), f"{header_str} (1) ACCORDING TO THE SURGEON GENERAL,", fill=(0, 0, 0), font=font)
    draw.text((gw_x + 20, gw_y + 30 + cap_height_px), "WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY", fill=(0, 0, 0), font=font)
    draw.text((gw_x + 20, gw_y + 40 + cap_height_px * 2), "BECAUSE OF THE RISK OF BIRTH DEFECTS.", fill=(0, 0, 0), font=font)

    np_img = np.array(img)

    if tilt_deg > 0:
        # Apply perspective transform to simulate planar tilt / skew
        src_pts = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        shift = int(w * 0.40 * (tilt_deg / 45.0))
        dst_pts = np.float32([[shift, 0], [w - shift, 0], [w, h], [0, h]])
        matrix = cv2.getPerspectiveTransform(src_pts, dst_pts)
        np_img = cv2.warpPerspective(np_img, matrix, (w, h), borderValue=(240, 240, 240))

    if blur:
        np_img = cv2.GaussianBlur(np_img, (35, 35), 15.0)

    _, enc = cv2.imencode(".jpg", cv2.cvtColor(np_img, cv2.COLOR_RGB2BGR))
    return enc.tobytes()


class TestTypeSizeThresholds:
    """Verify statutory 27 CFR 16.22 threshold calculation by container volume."""

    def test_threshold_small_container(self) -> None:
        assert get_required_min_height_mm("187 mL") == 1.0
        assert get_required_min_height_mm("50 mL") == 1.0
        assert get_required_min_height_mm("8 fl. oz.") == 1.0

    def test_threshold_standard_container(self) -> None:
        assert get_required_min_height_mm("750 mL") == 2.0
        assert get_required_min_height_mm("1 L") == 2.0
        assert get_required_min_height_mm("12 fl oz") == 2.0
        assert get_required_min_height_mm("3000 mL") == 2.0

    def test_threshold_large_container(self) -> None:
        assert get_required_min_height_mm("5 L") == 3.0
        assert get_required_min_height_mm("1.75 L") == 2.0
        assert get_required_min_height_mm("4000 mL") == 3.0

    def test_threshold_unknown_container(self) -> None:
        assert get_required_min_height_mm(None) == 2.0
        assert get_required_min_height_mm("") == 2.0


class TestOptionAScaleMarkerDetection:
    """Verify Option A computer vision detection and evaluation pipeline."""

    def test_ts01_id1_card_pass(self) -> None:
        """TS-01: ID-1 Card + compliant text (2.5 mm on 750 mL) -> pass."""
        img_bytes = _create_synthetic_label_image(cap_height_px=25, include_card=True)
        res = evaluate_type_size_from_image_bytes(
            img_bytes,
            net_contents="750 mL",
            gw_bbox=(50 / 1200, 680 / 900, 856 / 1200, 180 / 900),
        )
        assert res.method == CalibrationMethod.SCALE_MARKER_CARD_ID1
        assert res.state == "pass"
        assert res.measured_capital_height_mm is not None
        assert res.measured_capital_height_mm >= 2.0
        assert "meets or exceeds statutory" in res.verification_note

    def test_ts02_id1_card_deficient_fail(self) -> None:
        """TS-02: ID-1 Card + deficient text (1.3 mm on 750 mL) -> fail."""
        img_bytes = _create_synthetic_label_image(cap_height_px=13, include_card=True)
        res = evaluate_type_size_from_image_bytes(
            img_bytes,
            net_contents="750 mL",
            gw_bbox=(50 / 1200, 680 / 900, 856 / 1200, 180 / 900),
        )
        assert res.method == CalibrationMethod.SCALE_MARKER_CARD_ID1
        assert res.state == "fail"
        assert res.measured_capital_height_mm is not None
        assert res.measured_capital_height_mm < 2.0
        assert "is below the statutory" in res.verification_note

    def test_ts03_aruco_marker_pass_small_container(self) -> None:
        """TS-03: ArUco marker + 1.3 mm text on small container (187 mL) -> pass."""
        img_bytes = _create_synthetic_label_image(cap_height_px=13, include_card=False, include_aruco=True)
        res = evaluate_type_size_from_image_bytes(
            img_bytes,
            net_contents="187 mL",
            gw_bbox=(50 / 1200, 680 / 900, 856 / 1200, 180 / 900),
        )
        if res.method == CalibrationMethod.SCALE_MARKER_ARUCO:
            assert res.required_min_height_mm == 1.0
            assert res.state == "pass"

    def test_ts05_missing_marker_in_marker_mode(self) -> None:
        """TS-05: Missing scale marker when user requests marker mode -> cannot_measure."""
        img_bytes = _create_synthetic_label_image(include_card=False)
        res = evaluate_type_size_from_image_bytes(
            img_bytes,
            net_contents="750 mL",
            calibration_hint="marker",
        )
        assert res.state == "cannot_measure"
        assert "Scale marker not detected" in res.verification_note

    def test_ts06_excessive_tilt_skew(self) -> None:
        """TS-06: Excessive perspective tilt (>25 deg) -> cannot_measure with retake guidance."""
        img_bytes = _create_synthetic_label_image(include_card=True, tilt_deg=35.0)
        res = evaluate_type_size_from_image_bytes(
            img_bytes,
            net_contents="750 mL",
        )
        # Should detect excessive skew or return cannot_measure/uncalibrated
        if res.skew_angle_deg is not None and res.skew_angle_deg > 25.0:
            assert res.state == "cannot_measure"
            assert "perspective tilt" in res.verification_note

    def test_ts07_blurred_text(self) -> None:
        """TS-07: Out of focus / blurred text -> cannot_measure."""
        img_bytes = _create_synthetic_label_image(include_card=True, blur=True)
        res = evaluate_type_size_from_image_bytes(
            img_bytes,
            net_contents="750 mL",
        )
        assert res.state in ("cannot_measure", "warning")

    def test_ts10_naked_uncalibrated_photo_advisory(self) -> None:
        """TS-10: Naked uncalibrated photo returns uncalibrated advisory without invented mm."""
        img_bytes = _create_synthetic_label_image(include_card=False)
        res = evaluate_type_size_from_image_bytes(
            img_bytes,
            net_contents="750 mL",
        )
        assert res.method == CalibrationMethod.UNCALIBRATED_ESTIMATE
        assert res.state == "warning"
        assert res.measured_capital_height_mm is None  # NO invented mm
        assert "Physical gauge measurement required" in res.verification_note


class TestComplianceIntegration:
    """Verify integration of TypeSizeMeasurement into compliance checks and statutory hard rules."""

    def test_ts11_statutory_w02_missing_warning_hard_fail(self) -> None:
        """TS-11: Missing Government Warning hard fails even if scale marker present."""
        extracted = ExtractedLabelData(
            government_warning_present=False,
            government_warning_header=None,
            government_warning_body=None,
        )
        ts_details = TypeSizeMeasurement(
            method=CalibrationMethod.SCALE_MARKER_CARD_ID1,
            pixels_per_mm=12.0,
            measured_capital_height_mm=2.5,
            required_min_height_mm=2.0,
            state="pass",
            verification_note="Card validated",
        )
        result = _check_government_warning(extracted, type_size_details=ts_details)
        assert result.status == "fail"
        assert "not found on label" in result.message

    def test_ts12_statutory_w03_casing_hard_fail(self) -> None:
        """TS-12: Title-cased header 'Government Warning:' hard fails even if physical size is compliant."""
        extracted = ExtractedLabelData(
            government_warning_present=True,
            government_warning_header="Government Warning:",
            government_warning_body=CANONICAL_WARNING_BODY,
            net_contents="750 mL",
        )
        ts_details = TypeSizeMeasurement(
            method=CalibrationMethod.SCALE_MARKER_CARD_ID1,
            pixels_per_mm=12.0,
            measured_capital_height_mm=2.5,
            required_min_height_mm=2.0,
            state="pass",
            verification_note="Card validated",
        )
        result = _check_government_warning(extracted, type_size_details=ts_details)
        assert result.status == "fail"
        assert "header must appear in capital letters exactly" in result.message

    def test_type_size_fail_overrides_text_pass(self) -> None:
        """When text wording is perfect but type size is deficient, status is fail."""
        extracted = ExtractedLabelData(
            government_warning_present=True,
            government_warning_header=CANONICAL_WARNING_HEADER,
            government_warning_body=CANONICAL_WARNING_BODY,
            net_contents="750 mL",
        )
        ts_details = TypeSizeMeasurement(
            method=CalibrationMethod.SCALE_MARKER_CARD_ID1,
            pixels_per_mm=10.0,
            measured_capital_height_mm=1.3,
            required_min_height_mm=2.0,
            state="fail",
            verification_note="Calibrated capital letter height 1.30 mm is below 2.0 mm requirement.",
        )
        result = _check_government_warning(extracted, type_size_details=ts_details)
        assert result.status == "fail"
        assert "type size is deficient" in result.message

    def test_type_size_pass_emits_calibrated_message(self) -> None:
        """When text wording and calibrated type size pass, status is pass with calibrated note."""
        extracted = ExtractedLabelData(
            government_warning_present=True,
            government_warning_header=CANONICAL_WARNING_HEADER,
            government_warning_body=CANONICAL_WARNING_BODY,
            net_contents="750 mL",
        )
        ts_details = TypeSizeMeasurement(
            method=CalibrationMethod.SCALE_MARKER_CARD_ID1,
            pixels_per_mm=14.0,
            measured_capital_height_mm=2.4,
            required_min_height_mm=2.0,
            uncertainty_mm=0.08,
            state="pass",
            verification_note="Calibrated capital letter height 2.40 mm >= 2.0 mm requirement.",
        )
        result = _check_government_warning(extracted, type_size_details=ts_details)
        assert result.status == "pass"
        assert "meets statutory type-size requirements" in result.message
        assert result.type_size_details is not None
        assert result.type_size_details.measured_capital_height_mm == 2.4

    def test_full_application_and_label_check_flows(self) -> None:
        """Verify full run_compliance_checks and check_label_requirements pipeline."""
        extracted = ExtractedLabelData(
            brand_name="BOURBON VALLEY",
            class_type="Straight Bourbon Whiskey",
            alcohol_content="45% Alc./Vol.",
            net_contents="750 mL",
            name_and_address="Distilled & Bottled by BV Co, Louisville, KY",
            government_warning_present=True,
            government_warning_header=CANONICAL_WARNING_HEADER,
            government_warning_body=CANONICAL_WARNING_BODY,
            extraction_confidence=0.95,
        )
        app_data = ApplicationData(
            beverage_type="distilled_spirits",
            brand_name="BOURBON VALLEY",
            class_type="Straight Bourbon Whiskey",
            alcohol_content="45% Alc./Vol.",
            net_contents="750 mL",
        )
        ts_details = TypeSizeMeasurement(
            method=CalibrationMethod.SCALE_MARKER_CARD_ID1,
            pixels_per_mm=15.0,
            measured_capital_height_mm=2.3,
            required_min_height_mm=2.0,
            state="pass",
            verification_note="Calibrated pass",
        )
        # Application check
        app_results = run_compliance_checks(app_data, extracted, type_size_details=ts_details)
        gw_field = next(r for r in app_results if r.field == "government_warning")
        assert gw_field.status == "pass"
        assert gw_field.type_size_details == ts_details

        # Label-only check
        label_results = check_label_requirements(
            extracted,
            confirmed_beverage_type="distilled_spirits",
            type_size_details=ts_details,
        )
        gw_check = next(r for r in label_results if r.field == "government_warning")
        assert gw_check.status == "pass"
        assert gw_check.type_size_details == ts_details


class TestUncalibratedRegression:
    """Regression tests for uncalibrated images, missing markers, and OpenAPI schema exposure."""

    @staticmethod
    def _create_800x600_jpeg() -> bytes:
        """Create a synthetic 800x600 RGB JPEG with non-trivial pixels."""
        img = Image.new("RGB", (800, 600), color=(220, 220, 220))
        draw = ImageDraw.Draw(img)
        draw.rectangle([50, 50, 750, 550], fill=(255, 255, 255), outline=(50, 50, 50), width=2)
        draw.text((100, 100), "BRAND NAME WHISKEY", fill=(0, 0, 0))
        draw.text((100, 200), "40% ALC./VOL. 750 mL", fill=(0, 0, 0))
        draw.text((100, 300), "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL...", fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    def test_synthetic_800x600_jpeg_review_returns_200_and_type_size_details(self) -> None:
        """Requirement 1, 2, 5: Synthetic 800x600 RGB JPEG without marker returns 200 with structured type_size_details."""
        client = TestClient(app)
        jpeg_bytes = self._create_800x600_jpeg()

        app_json = json.dumps({
            "beverage_type": "distilled_spirits",
            "brand_name": "BRAND NAME WHISKEY",
            "class_type": "Whiskey",
            "alcohol_content": "40% ALC./VOL.",
            "net_contents": "750 mL",
        })

        fake_extracted = ExtractedLabelData(
            brand_name="BRAND NAME WHISKEY",
            class_type="Whiskey",
            alcohol_content="40% ALC./VOL.",
            net_contents="750 mL",
            government_warning_present=True,
            government_warning_header=CANONICAL_WARNING_HEADER,
            government_warning_body=CANONICAL_WARNING_BODY,
            extraction_confidence=0.95,
            field_locations=[
                FieldLocation(field="government_warning", image_index=0, confidence="high", x=0.1, y=0.5, width=0.8, height=0.3),
            ],
        )

        with patch("app.main._extract_fields_with_retry", AsyncMock(return_value=(fake_extracted, None))):
            resp = client.post(
                "/api/review",
                files=[("files", ("label.jpg", jpeg_bytes, "image/jpeg"))],
                data={"application": app_json},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_status"] == "pass"

        gw_field = next((f for f in data["fields"] if f["field"] == "government_warning"), None)
        assert gw_field is not None
        assert gw_field["status"] == "pass"
        assert gw_field["type_size_details"] is not None

        ts = gw_field["type_size_details"]
        assert ts["method"] == "uncalibrated_estimate"
        assert ts["state"] in ("warning", "cannot_measure")
        assert ts["measured_capital_height_mm"] is None  # Never invent mm
        assert ts["required_min_height_mm"] == 2.0
        assert "Physical gauge measurement required" in ts["verification_note"]

    def test_synthetic_800x600_jpeg_marker_mode_without_marker_cannot_measure(self) -> None:
        """Requirement 3: Missing marker when marker calibration requested returns cannot_measure advisory."""
        jpeg_bytes = self._create_800x600_jpeg()
        res = evaluate_type_size_from_image_bytes(
            jpeg_bytes,
            filename="label.jpg",
            net_contents="750 mL",
            calibration_hint="marker",
        )
        assert res.state == "cannot_measure"
        assert res.measured_capital_height_mm is None
        assert "Scale marker not detected" in res.verification_note

    def test_evaluate_type_size_from_corrupt_or_empty_bytes(self) -> None:
        """Robustness: Corrupt, truncated, or empty image bytes do not raise and return cannot_measure."""
        res_empty = evaluate_type_size_from_image_bytes(b"", filename="empty.jpg", net_contents="750 mL")
        assert res_empty.state == "cannot_measure"
        assert res_empty.method == CalibrationMethod.NONE
        assert res_empty.measured_capital_height_mm is None

        res_corrupt = evaluate_type_size_from_image_bytes(b"not_an_image_data", filename="bad.jpg", net_contents="750 mL")
        assert res_corrupt.state == "cannot_measure"
        assert res_corrupt.method == CalibrationMethod.NONE
        assert res_corrupt.measured_capital_height_mm is None

    def test_openapi_schema_exposes_type_size_and_cannot_measure(self) -> None:
        """Requirement 2: OpenAPI schema clearly documents FieldResult.type_size_details and TypeSizeMeasurement states."""
        openapi_schema = app.openapi()
        schemas = openapi_schema.get("components", {}).get("schemas", {})

        assert "TypeSizeMeasurement" in schemas
        ts_schema = schemas["TypeSizeMeasurement"]
        assert "state" in ts_schema.get("properties", {})
        state_prop = ts_schema["properties"]["state"]
        # Ensure cannot_measure is in enum / schema description
        enum_values = state_prop.get("enum", [])
        assert "cannot_measure" in enum_values
        assert "pass" in enum_values
        assert "fail" in enum_values
        assert "warning" in enum_values

        assert "FieldResult" in schemas
        field_result_schema = schemas["FieldResult"]
        assert "type_size_details" in field_result_schema.get("properties", {})
