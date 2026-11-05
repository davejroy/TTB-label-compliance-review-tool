"""Promote dest 59ebdd3: type-size NV, bottler-phrase match, origin, sulfite."""

from __future__ import annotations

from app.compliance import (
    CANONICAL_WARNING_BODY,
    CANONICAL_WARNING_HEADER,
    _check_government_warning,
    _check_label_country_of_origin,
    _check_text_field,
    overall_status,
    run_compliance_checks,
)
from app.cv_typesize import _geometry_type_size
from app.main import _prefer_type_size
from app.models import (
    ApplicationData,
    CalibrationMethod,
    ExtractedLabelData,
    TypeSizeMeasurement,
)
from app.claude_client import build_extraction_create_kwargs

import numpy as np
import pytest


def _extracted(**overrides) -> ExtractedLabelData:
    data = dict(
        brand_name="Hopline",
        class_type="India Pale Ale",
        alcohol_content="6.8% Alc./Vol.",
        net_contents="12 FL OZ",
        name_and_address="Hopline Brewing Co, Fort Collins, CO",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        origin_guess="domestic",
        beverage_type_guess="beer",
    )
    data.update(overrides)
    return ExtractedLabelData(**data)


def test_extraction_kwargs_omit_sampling_params():
    kwargs = build_extraction_create_kwargs(
        [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "Zg=="}}],
        "Record compact JSON fields from this label image.",
        "claude-sonnet-4-6",
    )
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
    assert "top_k" not in kwargs


def test_dropped_bottler_phrase_is_tolerated():
    result = _check_text_field(
        "name_and_address",
        "Name and Address",
        "Brewed and Canned by Hopline Brewing Co, Fort Collins, CO",
        "Hopline Brewing Co, Fort Collins, CO",
    )
    assert result.status == "pass"


def test_prefix_tolerance_requires_exact_remaining_name_and_place():
    result = _check_text_field(
        "name_and_address",
        "Name and Address",
        "Imported by Harbor Imports, Portland, OR",
        "Imported by Harbor Imports, Portland, WA",
    )
    assert result.status != "pass"


def test_imported_goods_company_is_not_an_importer_clause():
    result = _check_label_country_of_origin(
        _extracted(
            name_and_address="Imported Goods Co, Napa, CA",
            country_of_origin="",
            origin_guess="domestic",
        )
    )
    assert result.status == "pass"


def test_printed_imported_by_overrides_domestic_origin_guess():
    result = _check_label_country_of_origin(
        _extracted(
            name_and_address="Imported by Harbor Gateway Imports, New York, NY",
            country_of_origin="",
            origin_guess="domestic",
        )
    )
    assert result.status == "fail"
    assert "Imported by" in result.message


def test_geometry_below_threshold_is_needs_verification_not_fail():
    ts = TypeSizeMeasurement(
        method=CalibrationMethod.CONTAINER_GEOMETRY,
        pixels_per_mm=4.0,
        measured_capital_height_mm=1.5,
        required_min_height_mm=2.0,
        uncertainty_mm=0.2,
        state="needs_verification",
        verification_note="Uncalibrated container-geometry estimate 1.50 mm is below 2.0 mm.",
    )
    result = _check_government_warning(_extracted(), type_size_details=ts)
    assert result.status == "needs_verification"
    assert result.status != "fail"
    assert overall_status([result]) == "needs_verification"


def test_scale_marker_below_threshold_is_hard_fail():
    ts = TypeSizeMeasurement(
        method=CalibrationMethod.SCALE_MARKER_CARD_ID1,
        pixels_per_mm=12.0,
        measured_capital_height_mm=1.3,
        required_min_height_mm=2.0,
        state="fail",
        verification_note="Calibrated capital letter height 1.30 mm is below 2.0 mm.",
    )
    result = _check_government_warning(_extracted(), type_size_details=ts)
    assert result.status == "fail"


def test_geometry_estimate_function_never_fails():
    image = np.zeros((500, 500, 3), dtype=np.uint8)
    image[100:140, 80:420] = 255
    result = _geometry_type_size(
        image,
        1.0,
        (0.1, 0.2, 0.7, 0.08),
        "750 mL",
        2.0,
        25,
        "GOVERNMENT WARNING",
    )
    assert result.state != "fail"
    assert result.method in {
        CalibrationMethod.CONTAINER_GEOMETRY,
        CalibrationMethod.UNCALIBRATED_ESTIMATE,
    }


def test_wine_missing_sulfite_is_needs_verification():
    results = run_compliance_checks(
        ApplicationData(
            beverage_type="wine",
            brand_name="Crescent Ridge",
            class_type="Cabernet Sauvignon",
            alcohol_content="13.5% Alc./Vol.",
            net_contents="750 mL",
        ),
        _extracted(
            brand_name="Crescent Ridge",
            class_type="Cabernet Sauvignon",
            alcohol_content="13.5% Alc./Vol.",
            net_contents="750 mL",
            beverage_type_guess="wine",
            sulfite_declaration=None,
        ),
    )
    sulfite = next(item for item in results if item.field == "sulfite_declaration")
    assert sulfite.status == "needs_verification"
    assert "this product" in sulfite.message
    assert "27 CFR" not in sulfite.message


def _marker(state: str, note: str) -> TypeSizeMeasurement:
    return TypeSizeMeasurement(
        method=CalibrationMethod.SCALE_MARKER_CARD_ID1,
        required_min_height_mm=2.0,
        state=state,
        verification_note=note,
    )


def test_completed_marker_wins_over_geometry_preference():
    marker = _marker("fail", "marker fail")
    geometry = TypeSizeMeasurement(
        method=CalibrationMethod.CONTAINER_GEOMETRY,
        required_min_height_mm=2.0,
        state="needs_verification",
        verification_note="geometry nv",
    )
    incomplete = _marker("cannot_measure", "skew")
    later_pass = TypeSizeMeasurement(
        method=CalibrationMethod.SCALE_MARKER_ARUCO,
        required_min_height_mm=2.0,
        state="pass",
        verification_note="later marker pass",
    )
    results = [geometry, incomplete, marker, later_pass]
    completed_marker = [
        item
        for item in results
        if item.method
        in {
            CalibrationMethod.SCALE_MARKER_ARUCO,
            CalibrationMethod.SCALE_MARKER_CARD_ID1,
            CalibrationMethod.SCALE_MARKER_RULER,
        }
        and item.state in {"pass", "fail", "warning"}
    ]
    assert _prefer_type_size(completed_marker) is marker
    assert incomplete.state == "cannot_measure"


@pytest.mark.parametrize(
    "first,second,expected",
    [
        ("fail", "pass", "fail"),
        ("pass", "fail", "fail"),
        ("fail", "warning", "fail"),
        ("warning", "fail", "fail"),
        ("fail", "needs_verification", "fail"),
        ("needs_verification", "fail", "fail"),
        ("warning", "pass", "warning"),
        ("pass", "warning", "warning"),
        ("warning", "needs_verification", "warning"),
        ("needs_verification", "warning", "warning"),
        ("needs_verification", "pass", "needs_verification"),
        ("pass", "needs_verification", "needs_verification"),
        ("fail", "cannot_measure", "fail"),
        ("cannot_measure", "fail", "fail"),
        ("warning", "cannot_measure", "warning"),
        ("cannot_measure", "warning", "warning"),
        ("needs_verification", "cannot_measure", "needs_verification"),
        ("cannot_measure", "needs_verification", "needs_verification"),
        ("pass", "cannot_measure", "pass"),
        ("cannot_measure", "pass", "pass"),
    ],
)
def test_prefer_type_size_uses_severity_not_order(first, second, expected):
    left = _marker(first, f"first {first}")
    right = _marker(second, f"second {second}")
    assert _prefer_type_size([left, right]).state == expected
    assert _prefer_type_size([right, left]).state == expected
