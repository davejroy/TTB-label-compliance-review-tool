from app.compliance import (
    CANONICAL_WARNING_BODY,
    CANONICAL_WARNING_HEADER,
    check_label_requirements,
    merge_extracted_label_data,
    overall_status,
    run_compliance_checks,
)
from app.models import ApplicationData, ExtractedLabelData, FieldLocation


def make_application(**overrides) -> ApplicationData:
    base = dict(
        beverage_type="distilled_spirits",
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
    )
    base.update(overrides)
    return ApplicationData(**base)


def make_extracted(**overrides) -> ExtractedLabelData:
    base = dict(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
    )
    base.update(overrides)
    return ExtractedLabelData(**base)


def test_all_fields_match_pass():
    results = run_compliance_checks(make_application(), make_extracted())
    assert overall_status(results) == "pass"
    assert all(r.status == "pass" for r in results)


def test_brand_name_case_difference_is_pass():
    # Per Dave's feedback, "STONE'S THROW" vs "Stone's Throw" is the same brand -
    # case/punctuation differences alone should not be flagged.
    results = run_compliance_checks(
        make_application(brand_name="Stone's Throw"),
        make_extracted(brand_name="STONE'S THROW"),
    )
    brand_result = next(r for r in results if r.field == "brand_name")
    assert brand_result.status == "pass"


def test_brand_name_mismatch_is_fail():
    results = run_compliance_checks(
        make_application(brand_name="Old Tom Distillery"),
        make_extracted(brand_name="New Tom Distillery"),
    )
    brand_result = next(r for r in results if r.field == "brand_name")
    assert brand_result.status == "fail"


def test_missing_brand_name_on_label_review_fails():
    results = run_compliance_checks(
        make_application(brand_name="Old Tom Distillery"),
        make_extracted(brand_name=""),
    )
    brand_result = next(r for r in results if r.field == "brand_name")
    assert brand_result.status == "fail"
    assert "not found on label" in brand_result.message


def test_missing_brand_name_whitespace_only_fails():
    results = run_compliance_checks(
        make_application(brand_name="Old Tom Distillery"),
        make_extracted(brand_name="   "),
    )
    brand_result = next(r for r in results if r.field == "brand_name")
    assert brand_result.status == "fail"
    assert "not found on label" in brand_result.message


def test_missing_brand_name_none_value_fails():
    results = run_compliance_checks(
        make_application(brand_name="Old Tom Distillery"),
        make_extracted(brand_name=None),
    )
    brand_result = next(r for r in results if r.field == "brand_name")
    assert brand_result.status == "fail"
    assert "not found on label" in brand_result.message


def test_missing_brand_name_with_address_present_regression_w10():
    # Regression test for field corpus W10:
    # When label has no standalone brand name, presence of producer/bottler line
    # (name_and_address) must NOT allow brand_name to pass.
    results = run_compliance_checks(
        make_application(
            brand_name="Old Tom Distillery",
            name_and_address="Distilled and Bottled by Old Tom Distilling Co., Bardstown, KY",
        ),
        make_extracted(
            brand_name="",
            name_and_address="Distilled and Bottled by Old Tom Distilling Co., Bardstown, KY",
        ),
    )
    brand_result = next(r for r in results if r.field == "brand_name")
    assert brand_result.status == "fail"
    assert brand_result.status != "pass"
    assert overall_status(results) == "fail"


def test_missing_government_warning_fails():
    results = run_compliance_checks(
        make_application(),
        make_extracted(
            government_warning_present=False,
            government_warning_header="",
            government_warning_body="",
        ),
    )
    warning_result = next(r for r in results if r.field == "government_warning")
    assert warning_result.status == "fail"


def test_government_warning_title_case_header_is_fail():
    # Issue #6 W03: 27 CFR 16.21 / 16.22 strictly requires the heading to appear in
    # capital letters exactly as 'GOVERNMENT WARNING:'. Any other casing (e.g. 'Government Warning:')
    # is a statutory non-compliance and MUST hard fail.
    results = run_compliance_checks(
        make_application(),
        make_extracted(government_warning_header="Government Warning:"),
    )
    warning_result = next(r for r in results if r.field == "government_warning")
    assert warning_result.status == "fail"
    assert "capital letters" in warning_result.message


def test_alcohol_content_within_tolerance_is_warning():
    results = run_compliance_checks(
        make_application(alcohol_content="45% Alc./Vol."),
        make_extracted(alcohol_content="45.1% Alc./Vol."),
    )
    abv_result = next(r for r in results if r.field == "alcohol_content")
    assert abv_result.status == "warning"


def test_alcohol_content_outside_tolerance_is_fail():
    results = run_compliance_checks(
        make_application(alcohol_content="45% Alc./Vol."),
        make_extracted(alcohol_content="40% Alc./Vol."),
    )
    abv_result = next(r for r in results if r.field == "alcohol_content")
    assert abv_result.status == "fail"


def test_distilled_spirits_alcohol_content_at_tolerance_boundary_is_warning():
    # 27 CFR 5.65(c): +/-0.3 percentage points for distilled spirits.
    results = run_compliance_checks(
        make_application(alcohol_content="45% Alc./Vol."),
        make_extracted(alcohol_content="45.3% Alc./Vol."),
    )
    abv_result = next(r for r in results if r.field == "alcohol_content")
    assert abv_result.status == "warning"


def test_distilled_spirits_alcohol_content_just_outside_tolerance_is_fail():
    results = run_compliance_checks(
        make_application(alcohol_content="45% Alc./Vol."),
        make_extracted(alcohol_content="45.4% Alc./Vol."),
    )
    abv_result = next(r for r in results if r.field == "alcohol_content")
    assert abv_result.status == "fail"


def test_wine_alcohol_content_above_14pct_uses_1pt_tolerance():
    # 27 CFR 4.36(b)(1): +/-1.0 percentage point for wines over 14% ABV.
    results = run_compliance_checks(
        make_application(beverage_type="wine", alcohol_content="15% Alc./Vol."),
        make_extracted(alcohol_content="16% Alc./Vol."),
    )
    abv_result = next(r for r in results if r.field == "alcohol_content")
    assert abv_result.status == "warning"

    results = run_compliance_checks(
        make_application(beverage_type="wine", alcohol_content="15% Alc./Vol."),
        make_extracted(alcohol_content="16.1% Alc./Vol."),
    )
    abv_result = next(r for r in results if r.field == "alcohol_content")
    assert abv_result.status == "fail"


def test_wine_alcohol_content_at_or_below_14pct_uses_1_5pt_tolerance():
    # 27 CFR 4.36(b)(1): +/-1.5 percentage points for wines at or below 14% ABV.
    results = run_compliance_checks(
        make_application(beverage_type="wine", alcohol_content="12.5% Alc./Vol."),
        make_extracted(alcohol_content="14% Alc./Vol."),
    )
    abv_result = next(r for r in results if r.field == "alcohol_content")
    assert abv_result.status == "warning"

    results = run_compliance_checks(
        make_application(beverage_type="wine", alcohol_content="12.4% Alc./Vol."),
        make_extracted(alcohol_content="14% Alc./Vol."),
    )
    abv_result = next(r for r in results if r.field == "alcohol_content")
    assert abv_result.status == "fail"


def test_missing_alcohol_content_on_wine_is_warning():
    results = run_compliance_checks(
        make_application(beverage_type="wine", alcohol_content="13% Alc./Vol."),
        make_extracted(alcohol_content=""),
    )
    abv_result = next(r for r in results if r.field == "alcohol_content")
    assert abv_result.status == "warning"


def test_net_contents_mismatch_fails():
    results = run_compliance_checks(
        make_application(net_contents="750 mL"),
        make_extracted(net_contents="700 mL"),
    )
    nc_result = next(r for r in results if r.field == "net_contents")
    assert nc_result.status == "fail"


def test_net_contents_oz_vs_fl_oz_passes():
    results = run_compliance_checks(
        make_application(net_contents="12 oz"),
        make_extracted(net_contents="12 FL. OZ."),
    )
    nc_result = next(r for r in results if r.field == "net_contents")
    assert nc_result.status == "pass"


def test_government_warning_all_caps_body_passes():
    results = run_compliance_checks(
        make_application(),
        make_extracted(government_warning_body=CANONICAL_WARNING_BODY.upper()),
    )
    warning_result = next(r for r in results if r.field == "government_warning")
    assert warning_result.status == "pass"


def test_label_requirements_all_present_passes():
    results = check_label_requirements(
        make_extracted(
            brand_name="OLD TOM DISTILLERY",
            name_and_address="Old Tom Distillery, Bardstown, KY",
            field_locations=[
                FieldLocation(field="brand_name", x=0.5, y=0.1, width=0.4, height=0.1),
                FieldLocation(field="name_and_address", x=0.5, y=0.8, width=0.4, height=0.1),
            ],
        ),
        beverage_type="distilled_spirits",
    )
    # Formula-dependent checks return "warning" by design when label data is absent
    _formula_fields = {"sulfite_declaration", "allergen_statements", "age_statement", "commodity_statement"}
    assert all(r.status == "pass" for r in results if r.field not in _formula_fields | {"country_of_origin"})
    # country_of_origin and formula-dependent checks (allergen, age, commodity)
    # return "warning" - that keeps the overall status at "warning".
    coo = next(r for r in results if r.field == "country_of_origin")
    assert coo.status == "warning"
    assert overall_status(results) == "warning"


def test_label_requirements_missing_brand_name_fails():
    for empty_val in ("", "   ", None, "   -   "):
        results = check_label_requirements(
            make_extracted(brand_name=empty_val), beverage_type="distilled_spirits"
        )
        brand_result = next(r for r in results if r.field == "brand_name")
        assert brand_result.status == "fail"
        assert brand_result.status != "pass"
        assert "Brand Name was not found on the label" in brand_result.message


def test_label_requirements_missing_abv_on_spirits_fails():
    results = check_label_requirements(
        make_extracted(alcohol_content=""), beverage_type="distilled_spirits"
    )
    abv_result = next(r for r in results if r.field == "alcohol_content")
    assert abv_result.status == "fail"


def test_label_requirements_missing_abv_on_beer_passes():
    results = check_label_requirements(
        make_extracted(alcohol_content=""), beverage_type="beer"
    )
    abv_result = next(r for r in results if r.field == "alcohol_content")
    assert abv_result.status == "pass"


def test_label_requirements_missing_abv_on_wine_is_warning():
    results = check_label_requirements(
        make_extracted(alcohol_content=""), beverage_type="wine"
    )
    abv_result = next(r for r in results if r.field == "alcohol_content")
    assert abv_result.status == "warning"


def test_label_requirements_missing_government_warning_fails():
    results = check_label_requirements(
        make_extracted(
            government_warning_present=False,
            government_warning_header="",
            government_warning_body="",
        ),
        beverage_type="distilled_spirits",
    )
    warning_result = next(r for r in results if r.field == "government_warning")
    assert warning_result.status == "fail"


def test_label_requirements_country_of_origin_present_passes():
    results = check_label_requirements(
        make_extracted(country_of_origin="Product of Scotland"),
        beverage_type="distilled_spirits",
    )
    coo = next(r for r in results if r.field == "country_of_origin")
    assert coo.status == "pass"


def test_label_requirements_missing_country_of_origin_on_domestic_label_passes():
    results = check_label_requirements(
        make_extracted(country_of_origin="", origin_guess="domestic"),
        beverage_type="distilled_spirits",
    )
    coo = next(r for r in results if r.field == "country_of_origin")
    assert coo.status == "pass"


def test_label_requirements_missing_country_of_origin_on_imported_label_fails():
    results = check_label_requirements(
        make_extracted(country_of_origin="", origin_guess="imported"),
        beverage_type="distilled_spirits",
    )
    coo = next(r for r in results if r.field == "country_of_origin")
    assert coo.status == "fail"


def test_label_requirements_missing_country_of_origin_with_unknown_origin_is_warning():
    results = check_label_requirements(
        make_extracted(country_of_origin="", origin_guess="unknown"),
        beverage_type="distilled_spirits",
    )
    coo = next(r for r in results if r.field == "country_of_origin")
    assert coo.status == "warning"


# ---------------------------------------------------------------------------
# Confidence gate regression tests  (Item 6)
# ---------------------------------------------------------------------------
# These tests lock in the BEVERAGE_TOLERANCE and FIELD_CONFIDENCE_THRESHOLDS
# values that were calibrated on Jun 16 2026.  If anyone changes a threshold
# in compliance.py the test name makes the intent obvious, and the failure
# tells you exactly which boundary moved.
#
# Philosophy: test the *gate behaviour* (pass / raise), not the dict values
# directly, so the tests catch regressions in the logic as well as in the
# numbers.
#
# No API key or live images are needed; ExtractedLabelData is constructed
# in-memory with synthetic confidence scores.

import pytest
from app.compliance import (
    BEVERAGE_TOLERANCE,
    FIELD_CONFIDENCE_THRESHOLDS,
    LowConfidenceError,
    _DEFAULT_FIELD_CONFIDENCE,
    _DEFAULT_MIN_CONFIDENCE,
    assert_extraction_confidence,
)


# ---------------------------------------------------------------------------
# Helper: build an ExtractedLabelData with controlled confidence scores
# ---------------------------------------------------------------------------

def make_extracted_with_confidence(
    extraction_confidence: float,
    per_field: dict | None = None,
) -> "ExtractedLabelData":
    """Return an ExtractedLabelData whose confidence fields are set to the
    given values.  All compliance fields default to passing values so that
    only the confidence gate is being exercised."""
    from app.compliance import CANONICAL_WARNING_BODY, CANONICAL_WARNING_HEADER
    kwargs = dict(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        extraction_confidence=extraction_confidence,
        per_field_confidence=per_field or {},
    )
    return ExtractedLabelData(**kwargs)


# ---------------------------------------------------------------------------
# 1. BEVERAGE_TOLERANCE table — ensure expected keys and values are present
# ---------------------------------------------------------------------------

def test_beverage_tolerance_keys_present():
    """All four beverage classes must be defined."""
    assert set(BEVERAGE_TOLERANCE.keys()) == {"distilled_spirits", "wine", "beer", "unknown"}


def test_distilled_spirits_abv_tolerance_is_0_3():
    """27 CFR 5.65(c) mandates ±0.3 pp for spirits — lock that in."""
    assert BEVERAGE_TOLERANCE["distilled_spirits"]["abv_tolerance"] == 0.3


def test_beer_abv_tolerance_is_0_3():
    """27 CFR 7.65(c) mandates ±0.3 pp for malt beverages."""
    assert BEVERAGE_TOLERANCE["beer"]["abv_tolerance"] == 0.3


def test_wine_abv_tolerance_is_1_0():
    """27 CFR 4.36(b)(1) baseline fallback: 1.0 pp (>14% ABV band)."""
    assert BEVERAGE_TOLERANCE["wine"]["abv_tolerance"] == 1.0


def test_distilled_spirits_min_confidence_is_0_50():
    assert BEVERAGE_TOLERANCE["distilled_spirits"]["min_confidence"] == 0.50


def test_beer_min_confidence_is_0_45():
    assert BEVERAGE_TOLERANCE["beer"]["min_confidence"] == 0.45


def test_unknown_min_confidence_is_stricter_than_default():
    """Unknown beverage type should require *higher* confidence than known types."""
    assert BEVERAGE_TOLERANCE["unknown"]["min_confidence"] > _DEFAULT_MIN_CONFIDENCE


# ---------------------------------------------------------------------------
# 2. FIELD_CONFIDENCE_THRESHOLDS — key fields have expected floor values
# ---------------------------------------------------------------------------

def test_brand_name_threshold_is_0_35():
    assert FIELD_CONFIDENCE_THRESHOLDS["brand_name"] == 0.35


def test_alcohol_content_threshold_is_0_30():
    assert FIELD_CONFIDENCE_THRESHOLDS["alcohol_content"] == 0.30


def test_government_warning_body_threshold_is_lower_than_brand_name():
    """Gov Warning body is long curved text — its floor must be lower than
    the short brand-name floor to avoid false rejects on good photos."""
    assert FIELD_CONFIDENCE_THRESHOLDS["government_warning_body"] < FIELD_CONFIDENCE_THRESHOLDS["brand_name"]


# ---------------------------------------------------------------------------
# 3. assert_extraction_confidence gate — behavioural regression tests
# ---------------------------------------------------------------------------

def test_confidence_gate_passes_at_threshold():
    """Exactly at the threshold (not below) — should NOT raise."""
    threshold = BEVERAGE_TOLERANCE["distilled_spirits"]["min_confidence"]  # 0.50
    ext = make_extracted_with_confidence(extraction_confidence=threshold)
    assert_extraction_confidence(ext, beverage_type="distilled_spirits")  # must not raise


def test_confidence_gate_raises_just_below_threshold():
    """One hundredth below the threshold — must raise LowConfidenceError."""
    threshold = BEVERAGE_TOLERANCE["distilled_spirits"]["min_confidence"]  # 0.50
    ext = make_extracted_with_confidence(extraction_confidence=threshold - 0.01)
    with pytest.raises(LowConfidenceError) as exc_info:
        assert_extraction_confidence(ext, beverage_type="distilled_spirits")
    assert exc_info.value.confidence == pytest.approx(threshold - 0.01)
    assert exc_info.value.threshold == threshold


def test_confidence_gate_passes_with_no_score():
    """None confidence means the model didn't emit a score — gate skips silently."""
    ext = make_extracted_with_confidence(extraction_confidence=None)
    assert_extraction_confidence(ext, beverage_type="distilled_spirits")  # must not raise


def test_confidence_gate_beer_uses_lower_threshold_than_spirits():
    """Beer threshold (0.45) is lower than spirits (0.50).
    A score of 0.47 should pass for beer but fail for spirits."""
    score = 0.47
    beer_ext = make_extracted_with_confidence(extraction_confidence=score)
    spirits_ext = make_extracted_with_confidence(extraction_confidence=score)
    # Beer: 0.47 >= 0.45 → pass
    assert_extraction_confidence(beer_ext, beverage_type="beer")
    # Spirits: 0.47 < 0.50 → fail
    with pytest.raises(LowConfidenceError):
        assert_extraction_confidence(spirits_ext, beverage_type="distilled_spirits")


def test_per_field_confidence_raises_on_low_brand_name():
    """A per-field brand_name score below its threshold must raise LowConfidenceError."""
    threshold = FIELD_CONFIDENCE_THRESHOLDS["brand_name"]  # 0.35
    ext = make_extracted_with_confidence(
        extraction_confidence=0.90,  # overall fine
        per_field={"brand_name": threshold - 0.01},  # brand_name too low
    )
    with pytest.raises(LowConfidenceError) as exc_info:
        assert_extraction_confidence(ext, beverage_type="distilled_spirits")
    assert "brand name" in exc_info.value.user_message.lower()


def test_per_field_confidence_passes_at_brand_name_threshold():
    """Exactly at brand_name threshold — should pass."""
    threshold = FIELD_CONFIDENCE_THRESHOLDS["brand_name"]  # 0.35
    ext = make_extracted_with_confidence(
        extraction_confidence=0.90,
        per_field={"brand_name": threshold},
    )
    assert_extraction_confidence(ext, beverage_type="distilled_spirits")


def test_country_of_origin_zero_score_does_not_raise():
    """Domestic labels have no country_of_origin — a zero score must be
    skipped rather than treated as a low-confidence field (regression guard
    for commits 763cc78 / 37c7453)."""
    ext = make_extracted_with_confidence(
        extraction_confidence=0.90,
        per_field={"country_of_origin": 0.0, "brand_name": 0.60},
    )
    assert_extraction_confidence(ext, beverage_type="distilled_spirits")  # must not raise


def test_gov_warning_body_low_confidence_raises():
    """Gov Warning body below 0.20 must still raise — it just has a lower bar
    than brand_name, not *no* bar."""
    threshold = FIELD_CONFIDENCE_THRESHOLDS["government_warning_body"]  # 0.20
    ext = make_extracted_with_confidence(
        extraction_confidence=0.90,
        per_field={"government_warning_body": threshold - 0.01},
    )
    with pytest.raises(LowConfidenceError):
        assert_extraction_confidence(ext, beverage_type="distilled_spirits")


# ---------------------------------------------------------------------------
# Multi-photo merge and sulfite declaration tests
# ---------------------------------------------------------------------------

def test_multi_photo_merge_sulfite_declaration_from_back_photo_wins():
    """When front+back photo_roles are used, sulfite_declaration on the back
    label must win over empty string on front label, even if front label has
    a higher overall extraction confidence score.
    """
    front = ExtractedLabelData(
        brand_name="CHATEAU TEST",
        class_type="Red Wine",
        alcohol_content="13.5% ABV",
        net_contents="750 mL",
        sulfite_declaration="",  # absent on front photo
        beverage_type_guess="wine",
        government_warning_present=False,
        extraction_confidence=0.95,
        per_field_confidence={"brand_name": 0.95, "alcohol_content": 0.95},
    )
    back = ExtractedLabelData(
        brand_name="",
        class_type="",
        alcohol_content="",
        net_contents="",
        sulfite_declaration="CONTAINS SULFITES",  # present on back photo
        beverage_type_guess="wine",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        extraction_confidence=0.75,
        per_field_confidence={
            "sulfite_declaration": 0.75,
            "government_warning_body": 0.65,
        },
    )

    merged = merge_extracted_label_data([front, back], photo_roles=["front", "back"])
    assert merged.sulfite_declaration == "CONTAINS SULFITES"
    assert merged.brand_name == "CHATEAU TEST"
    assert merged.government_warning_present is True

    # Validate compliance check on merged extraction evaluates sulfite check as pass
    results = check_label_requirements(merged, beverage_type="wine")
    sulfite_res = next(r for r in results if r.field == "sulfite_declaration")
    assert sulfite_res.status == "pass"
    assert "Sulfite declaration found on label" in sulfite_res.message


def test_multi_photo_merge_sulfite_declaration_from_front_photo_wins():
    """If sulfite declaration is on front photo and back has empty string,
    the front declaration must win.
    """
    front = ExtractedLabelData(
        brand_name="CHATEAU FRONT",
        sulfite_declaration="Contains Sulfiting Agents",
        beverage_type_guess="wine",
        extraction_confidence=0.70,
    )
    back = ExtractedLabelData(
        brand_name="CHATEAU FRONT",
        sulfite_declaration="",
        beverage_type_guess="wine",
        extraction_confidence=0.92,
    )

    merged = merge_extracted_label_data([front, back], photo_roles=["front", "back"])
    assert merged.sulfite_declaration == "Contains Sulfiting Agents"


def test_multi_photo_merge_sulfite_declaration_both_present_picks_higher_confidence():
    """If both front and back photos extract a non-empty sulfite declaration,
    the higher confidence score should break the tie.
    """
    front = ExtractedLabelData(
        sulfite_declaration="Contains Sulfites (Low Conf)",
        extraction_confidence=0.60,
        per_field_confidence={"sulfite_declaration": 0.60},
    )
    back = ExtractedLabelData(
        sulfite_declaration="Contains Sulfites (High Conf)",
        extraction_confidence=0.88,
        per_field_confidence={"sulfite_declaration": 0.88},
    )

    merged = merge_extracted_label_data([front, back], photo_roles=["front", "back"])
    assert merged.sulfite_declaration == "Contains Sulfites (High Conf)"


def test_multi_photo_merge_sulfite_declaration_absent_on_all_photos():
    """If no photo has a sulfite declaration, merged value remains empty."""
    front = ExtractedLabelData(
        brand_name="NO SULFITE WINE",
        sulfite_declaration="",
        beverage_type_guess="wine",
        extraction_confidence=0.90,
    )
    back = ExtractedLabelData(
        sulfite_declaration="",
        beverage_type_guess="wine",
        extraction_confidence=0.85,
    )

    merged = merge_extracted_label_data([front, back], photo_roles=["front", "back"])
    assert merged.sulfite_declaration == ""


# ---------------------------------------------------------------------------
# Issue #9 P1: Brand Name in Address False-Pass Guard Tests
# ---------------------------------------------------------------------------

def test_brand_name_in_address_without_distinct_evidence_is_warning():
    """Issue #9 P1: Extracted brand_name contained in producer/bottler address line
    without distinct brand heading evidence must NOT clean-pass; must return warning.
    """
    app = make_application(
        brand_name="BUFFALO TRACE",
        name_and_address="Distilled and Bottled by Buffalo Trace Distillery, Frankfort, KY",
    )
    ext = make_extracted(
        brand_name="BUFFALO TRACE",
        name_and_address="Distilled and Bottled by Buffalo Trace Distillery, Frankfort, KY",
        field_locations=[],
        notes="",
    )
    results = run_compliance_checks(app, ext)
    brand_res = next(r for r in results if r.field == "brand_name")
    assert brand_res.status == "warning"
    assert "appears to be contained within or derived from" in brand_res.message
    assert "Requires human review" in brand_res.message


def test_brand_name_in_address_label_only_check_without_distinct_evidence_is_warning():
    """Issue #9 P1: Label-only check flags brand name derived from address without distinct evidence."""
    ext = make_extracted(
        brand_name="BUFFALO TRACE",
        name_and_address="Distilled and Bottled by Buffalo Trace Distillery, Frankfort, KY",
        field_locations=[],
        notes="",
    )
    results = check_label_requirements(ext, beverage_type="distilled_spirits")
    brand_res = next(r for r in results if r.field == "brand_name")
    assert brand_res.status == "warning"
    assert "Requires human review" in brand_res.message


def test_brand_name_in_address_with_spatial_separation_passes():
    """Issue #9 P1: If distinct brand heading spatial evidence exists, brand clean-passes."""
    app = make_application(
        brand_name="BUFFALO TRACE",
        name_and_address="Distilled and Bottled by Buffalo Trace Distillery, Frankfort, KY",
    )
    ext = make_extracted(
        brand_name="BUFFALO TRACE",
        name_and_address="Distilled and Bottled by Buffalo Trace Distillery, Frankfort, KY",
        field_locations=[
            FieldLocation(field="brand_name", x=0.5, y=0.15, width=0.4, height=0.1),
            FieldLocation(field="name_and_address", x=0.5, y=0.85, width=0.4, height=0.1),
        ],
    )
    results = run_compliance_checks(app, ext)
    brand_res = next(r for r in results if r.field == "brand_name")
    assert brand_res.status == "pass"


def test_brand_name_in_address_with_notes_evidence_passes():
    """Issue #9 P1: If notes mention distinct brand heading evidence, brand clean-passes."""
    app = make_application(
        brand_name="BUFFALO TRACE",
        name_and_address="Distilled and Bottled by Buffalo Trace Distillery, Frankfort, KY",
    )
    ext = make_extracted(
        brand_name="BUFFALO TRACE",
        name_and_address="Distilled and Bottled by Buffalo Trace Distillery, Frankfort, KY",
        notes="Distinct brand heading prominent on front label header.",
    )
    results = run_compliance_checks(app, ext)
    brand_res = next(r for r in results if r.field == "brand_name")
    assert brand_res.status == "pass"


def test_brand_name_distinct_from_address_passes():
    """Brand name that is not contained in address clean-passes without extra evidence."""
    app = make_application(
        brand_name="EAGLE RARE",
        name_and_address="Distilled and Bottled by Buffalo Trace Distillery, Frankfort, KY",
    )
    ext = make_extracted(
        brand_name="EAGLE RARE",
        name_and_address="Distilled and Bottled by Buffalo Trace Distillery, Frankfort, KY",
    )
    results = run_compliance_checks(app, ext)
    brand_res = next(r for r in results if r.field == "brand_name")
    assert brand_res.status == "pass"


# ---------------------------------------------------------------------------
# Issue #5 W02: Missing Government Warning Structured Failure Tests
# ---------------------------------------------------------------------------

def test_missing_gw_returns_fail_without_low_confidence_error():
    """Issue #5 W02: Missing Government Warning emits FieldResult status=fail
    and does NOT raise LowConfidenceError even when per_field_confidence is 0.0.
    """
    app = make_application()
    ext = make_extracted(
        government_warning_present=False,
        government_warning_header="",
        government_warning_body="",
        extraction_confidence=0.90,
        per_field_confidence={
            "brand_name": 0.95,
            "alcohol_content": 0.95,
            "net_contents": 0.95,
            "government_warning_header": 0.0,
            "government_warning_body": 0.0,
        },
    )
    results = run_compliance_checks(app, ext)
    gw_res = next(r for r in results if r.field == "government_warning")
    assert gw_res.status == "fail"
    assert "Government Warning statement not found on label" in gw_res.message
    assert "retake" not in gw_res.message.lower()


def test_missing_gw_label_only_check_returns_fail():
    """Issue #5 W02: In label-only check, missing Government Warning emits FieldResult status=fail."""
    ext = make_extracted(
        government_warning_present=False,
        government_warning_header=None,
        government_warning_body=None,
        per_field_confidence={"government_warning_body": 0.0},
    )
    results = check_label_requirements(ext, beverage_type="distilled_spirits")
    gw_res = next(r for r in results if r.field == "government_warning")
    assert gw_res.status == "fail"
    assert "Government Warning statement not found on label" in gw_res.message


# ---------------------------------------------------------------------------
# Issue #6 W03: Strict Government Warning Casing Hard-Fail Tests
# ---------------------------------------------------------------------------

def test_gw_header_casing_strict_fails():
    """Issue #6 W03: Any deviation from 'GOVERNMENT WARNING:' must hard-fail under 27 CFR 16.21/16.22."""
    for bad_header in ("Government Warning:", "government warning:", "GOVERNMENT WARNING", "GOVERNMENT WARNING -"):
        ext = make_extracted(government_warning_header=bad_header)
        results = run_compliance_checks(make_application(), ext)
        gw_res = next(r for r in results if r.field == "government_warning")
        assert gw_res.status == "fail", f"Expected fail for header '{bad_header}', got {gw_res.status}"
        assert "capital letters" in gw_res.message


# ---------------------------------------------------------------------------
# Issue D: T.D. TTB-200 Standards of Fill Tests (27 CFR 4.72 & 5.203)
# ---------------------------------------------------------------------------

def test_ttb_200_wine_authorized_sizes_pass():
    """Issue D: All authorized wine sizes under 27 CFR 4.72 / T.D. TTB-200 must PASS."""
    # Full list of authorized wine sizes:
    # 3 L, 2.25 L, 1.8 L, 1.5 L, 1 L, 750, 720, 700, 620, 600, 568, 550, 500,
    # 473, 375, 360, 355, 330, 300, 250, 200, 187, 180, 100, 50 mL (+ >4L whole liters)
    authorized_wine_sizes = [
        "50 mL", "100 mL", "180 mL", "187 mL", "200 mL", "250 mL", "300 mL",
        "330 mL", "355 mL", "360 mL", "375 mL", "473 mL", "500 mL", "550 mL",
        "568 mL", "600 mL", "620 mL", "700 mL", "720 mL", "750 mL", "1 L",
        "1.5 L", "1.8 L", "2.25 L", "3 L", "4 L", "5 L",
    ]
    for size in authorized_wine_sizes:
        ext = make_extracted(net_contents=size)
        results = check_label_requirements(ext, beverage_type="wine")
        net_res = next(r for r in results if r.field == "net_contents")
        assert net_res.status == "pass", f"Expected pass for wine size '{size}', got {net_res.status}: {net_res.message}"


def test_ttb_200_spirits_authorized_sizes_pass():
    """Issue D: All authorized spirits sizes under 27 CFR 5.203 / T.D. TTB-200 must PASS."""
    # Full list of authorized spirits sizes:
    # 3.75 L, 3 L, 2 L, 1.8 L, 1.75 L, 1.5 L, 1 L, 945, 900, 750, 720, 710, 700,
    # 570, 500, 475, 375, 355, 350, 331, 250, 200, 187, 100, 50 mL
    authorized_spirits_sizes = [
        "50 mL", "100 mL", "187 mL", "200 mL", "250 mL", "331 mL", "350 mL",
        "355 mL", "375 mL", "475 mL", "500 mL", "570 mL", "700 mL", "710 mL",
        "720 mL", "750 mL", "900 mL", "945 mL", "1 L", "1.5 L", "1.75 L",
        "1.8 L", "2 L", "3 L", "3.75 L",
    ]
    for size in authorized_spirits_sizes:
        ext = make_extracted(net_contents=size)
        results = check_label_requirements(ext, beverage_type="distilled_spirits")
        net_res = next(r for r in results if r.field == "net_contents")
        assert net_res.status == "pass", f"Expected pass for spirits size '{size}', got {net_res.status}: {net_res.message}"


def test_illegal_fill_sizes_fail():
    """Issue D: Non-authorized sizes under 27 CFR 4.72 / 5.203 must FAIL."""
    illegal_wine_sizes = ["800 mL", "650 mL", "400 mL", "4.5 L"]
    for size in illegal_wine_sizes:
        ext = make_extracted(net_contents=size)
        results = check_label_requirements(ext, beverage_type="wine")
        net_res = next(r for r in results if r.field == "net_contents")
        assert net_res.status == "fail", f"Expected fail for wine size '{size}', got {net_res.status}"

    illegal_spirits_sizes = ["800 mL", "650 mL", "400 mL", "4.5 L", "2.25 L"]
    for size in illegal_spirits_sizes:
        ext = make_extracted(net_contents=size)
        results = check_label_requirements(ext, beverage_type="distilled_spirits")
        net_res = next(r for r in results if r.field == "net_contents")
        assert net_res.status == "fail", f"Expected fail for spirits size '{size}', got {net_res.status}"


def test_malt_beverage_fill_sizes_unrestricted():
    """Issue D: Malt beverages (beer) under 27 CFR 7.70 are unrestricted in fill size."""
    beer_sizes = ["12 fl oz", "16 fl oz", "19.2 fl oz", "800 mL", "650 mL", "355 mL", "500 mL"]
    for size in beer_sizes:
        ext = make_extracted(net_contents=size)
        results = check_label_requirements(ext, beverage_type="beer")
        net_res = next(r for r in results if r.field == "net_contents")
        assert net_res.status == "pass", f"Expected pass for beer size '{size}', got {net_res.status}"


# ---------------------------------------------------------------------------
# Issue E: 27 CFR 16.22 Honesty Advisory Tests
# ---------------------------------------------------------------------------

def test_government_warning_pass_message_includes_16_22_type_size_honesty_note():
    """Issue E: Government Warning passing message must honestly disclose that
    physical type size in mm (27 CFR 16.22) is not verified from photos.
    """
    results = run_compliance_checks(make_application(), make_extracted())
    gw_res = next(r for r in results if r.field == "government_warning")
    assert gw_res.status == "pass"
    assert "27 CFR 16.22" in gw_res.message
    assert "gauge measurement" in gw_res.message or "physical" in gw_res.message
