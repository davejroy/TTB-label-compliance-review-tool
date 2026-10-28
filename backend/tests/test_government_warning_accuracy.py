"""27 CFR 16.21 / 16.22 Government Warning accuracy tests (Roadmap PR D)."""

from app.compliance import (
    CANONICAL_WARNING_BODY,
    CANONICAL_WARNING_FULL,
    CANONICAL_WARNING_HEADER,
    _check_government_warning,
    diff_warning_words,
    merge_extracted_label_data,
    run_compliance_checks,
)
from app.cv_typesize import get_max_characters_per_inch, get_required_min_height_mm
from app.models import (
    ApplicationData,
    CalibrationMethod,
    ExtractedLabelData,
    TypeSizeMeasurement,
)

SHORT_FORM_BODY = (
    "According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. "
    "Consumption of alcoholic beverages impairs your ability to drive a "
    "car or operate machinery, and may cause health problems."
)


def _extracted(**overrides) -> ExtractedLabelData:
    base = dict(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
    )
    base.update(overrides)
    return ExtractedLabelData(**base)


def _application(**overrides) -> ApplicationData:
    base = dict(
        beverage_type="distilled_spirits",
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
    )
    base.update(overrides)
    return ApplicationData(**base)


def _gw(extracted, type_size_details=None):
    return _check_government_warning(extracted, type_size_details=type_size_details)


# ---------------------------------------------------------------------------
# Word-level 16.21 diff
# ---------------------------------------------------------------------------

class TestWarningWordDiff:
    def test_exact_match(self):
        diff = diff_warning_words(CANONICAL_WARNING_FULL, CANONICAL_WARNING_FULL)
        assert diff.exact_match is True
        assert diff.missing_words == []
        assert diff.extra_words == []
        assert diff.changed_words == []
        assert diff.expected_words == diff.observed_words

    def test_missing_word(self):
        observed = CANONICAL_WARNING_FULL.replace(" women ", " ")
        diff = diff_warning_words(CANONICAL_WARNING_FULL, observed)
        assert diff.exact_match is False
        assert any(item.expected == "women" for item in diff.missing_words)
        missing = next(item for item in diff.missing_words if item.expected == "women")
        assert missing.expected_index is not None
        assert missing.kind == "missing"

    def test_substituted_word(self):
        observed = CANONICAL_WARNING_FULL.replace("women", "persons")
        diff = diff_warning_words(CANONICAL_WARNING_FULL, observed)
        assert diff.exact_match is False
        assert len(diff.changed_words) == 1
        change = diff.changed_words[0]
        assert change.expected == "women"
        assert change.observed == "persons"
        assert change.expected_index is not None
        assert change.observed_index is not None

    def test_ocr_whitespace_and_quote_noise(self):
        noisy = (
            "GOVERNMENT WARNING:\n(1) According to the Surgeon General,  women "
            "should not drink alcoholic\nbeverages during pregnancy because of "
            "the risk of birth defects.\n(2) Consumption of alcoholic beverages "
            "impairs your ability to drive a car or operate machinery, and may "
            "cause health problems."
        )
        curly = noisy.replace("'", "\u2019").replace('"', "\u201d")
        hyphenated = curly.replace("beverages", "bever-\nages")
        diff = diff_warning_words(CANONICAL_WARNING_FULL, hyphenated)
        assert diff.exact_match is True

    def test_lowercase_header_is_not_an_exact_caps_match(self):
        observed = "Government Warning: " + CANONICAL_WARNING_BODY
        diff = diff_warning_words(CANONICAL_WARNING_FULL, observed)
        # Word identity matches case-insensitively; casing is enforced separately.
        assert diff.exact_match is True
        result = _gw(_extracted(government_warning_header="Government Warning:"))
        assert result.status == "fail"
        assert "capital letters" in result.message
        assert result.warning_text_diff is not None
        assert result.warning_text_diff.exact_match is True


# ---------------------------------------------------------------------------
# Container sizes: full 16.21 text, 16.22 type-size / CPI tiers
# ---------------------------------------------------------------------------

class TestContainerSizeTiers:
    CASES = (
        ("50 mL", 1.0, 40),
        ("237 mL", 1.0, 40),
        ("750 mL", 2.0, 25),
        ("3.75 L", 3.0, 12),
    )

    def test_statutory_tiers(self):
        for size, min_mm, max_cpi in self.CASES:
            assert get_required_min_height_mm(size) == min_mm, size
            assert get_max_characters_per_inch(size) == max_cpi, size

    def test_full_statutory_text_required_at_each_size(self):
        for size, min_mm, max_cpi in self.CASES:
            result = _gw(_extracted(net_contents=size))
            assert result.status == "pass", size
            assert result.warning_text_diff is not None
            assert result.warning_text_diff.exact_match is True
            assert result.warning_format is not None
            assert result.warning_format.required_min_height_mm == min_mm
            assert result.warning_format.max_characters_per_inch == max_cpi
            assert "16.21(c)" not in result.message
            assert "short-form" not in result.message.lower()

    def test_short_form_fails_on_50ml(self):
        result = _gw(_extracted(net_contents="50 mL", government_warning_body=SHORT_FORM_BODY))
        assert result.status == "fail"
        assert result.warning_text_diff is not None
        assert result.warning_text_diff.exact_match is False
        missing = {item.expected for item in result.warning_text_diff.missing_words}
        assert "(1)" in missing
        assert "(2)" in missing
        assert "16.21(c)" not in result.message
        assert "short-form" not in result.message.lower()

    def test_short_form_fails_on_every_listed_size(self):
        for size, _, _ in self.CASES:
            result = _gw(_extracted(net_contents=size, government_warning_body=SHORT_FORM_BODY))
            assert result.status == "fail", size
            assert result.warning_text_diff is not None
            missing = {item.expected for item in result.warning_text_diff.missing_words}
            assert "(1)" in missing and "(2)" in missing


# ---------------------------------------------------------------------------
# Bold header advisory (never a hard fail)
# ---------------------------------------------------------------------------

class TestBoldHeaderFail:
    def test_not_bold_is_a_hard_fail(self):
        result = _gw(_extracted(government_warning_header_appears_bold=False))
        assert result.status == "fail"
        assert result.warning_format is not None
        assert result.warning_format.status == "fail"
        bold = next(f for f in result.warning_format.findings if f.code == "header_bold")
        assert bold.status == "fail"
        assert bold.citation == "27 CFR 16.22(a)(2)"
        assert "bold" in bold.message.lower()

    def test_bold_true_or_unknown_does_not_advisory(self):
        for value in (True, None):
            result = _gw(_extracted(government_warning_header_appears_bold=value))
            assert result.status == "pass"
            assert result.warning_format is not None
            assert result.warning_format.status == "pass"
            assert not any(f.code == "header_bold" for f in result.warning_format.findings)

    def test_header_caps_uses_header_field_not_body_prefix(self):
        result = _gw(
            _extracted(
                government_warning_header=CANONICAL_WARNING_HEADER,
                government_warning_body="Government Warning: " + CANONICAL_WARNING_BODY,
            )
        )
        assert result.status == "pass"
        assert result.warning_text_diff is not None
        assert result.warning_text_diff.exact_match is True

    def test_merge_ignores_bold_from_photos_without_warning(self):
        front = ExtractedLabelData(
            government_warning_present=False,
            government_warning_header="",
            government_warning_body="",
            government_warning_header_appears_bold=False,
        )
        back = ExtractedLabelData(
            government_warning_present=True,
            government_warning_header=CANONICAL_WARNING_HEADER,
            government_warning_body=CANONICAL_WARNING_BODY,
            government_warning_header_appears_bold=None,
        )
        merged = merge_extracted_label_data([front, back], photo_roles=["front", "back"])
        assert merged.government_warning_present is True
        assert merged.government_warning_header_appears_bold is None
        result = _gw(merged)
        assert result.status == "pass"
        assert not any(f.code == "header_bold" for f in result.warning_format.findings)

    def test_bold_advisory_does_not_override_text_fail(self):
        result = _gw(
            _extracted(
                government_warning_header="Government Warning:",
                government_warning_header_appears_bold=False,
            )
        )
        assert result.status == "fail"
        assert result.warning_format.status == "fail"


# ---------------------------------------------------------------------------
# Characters-per-inch advisory + format rollup
# ---------------------------------------------------------------------------

class TestCharactersPerInchAndRollup:
    def test_cpi_within_limit_is_still_advisory(self):
        ts = TypeSizeMeasurement(
            method=CalibrationMethod.SCALE_MARKER_CARD_ID1,
            pixels_per_mm=12.0,
            measured_capital_height_mm=2.2,
            required_min_height_mm=2.0,
            max_characters_per_inch=25,
            estimated_characters_per_inch=20.0,
            state="pass",
            verification_note="Calibrated capital letter height 2.20 mm meets 2.0 mm.",
        )
        result = _gw(_extracted(net_contents="750 mL"), type_size_details=ts)
        assert result.status == "pass"
        assert result.warning_format.status == "advisory"
        cpi = next(f for f in result.warning_format.findings if f.code == "characters_per_inch")
        assert cpi.status == "advisory"
        assert cpi.estimated is True
        assert cpi.citation == "27 CFR 16.22(a)(4)"
        assert "advisory only" in cpi.message

    def test_cpi_over_limit_is_advisory(self):
        ts = TypeSizeMeasurement(
            method=CalibrationMethod.SCALE_MARKER_CARD_ID1,
            pixels_per_mm=12.0,
            measured_capital_height_mm=2.2,
            required_min_height_mm=2.0,
            max_characters_per_inch=25,
            estimated_characters_per_inch=32.4,
            state="pass",
            verification_note="Calibrated capital letter height 2.20 mm meets 2.0 mm.",
        )
        result = _gw(_extracted(net_contents="750 mL"), type_size_details=ts)
        assert result.status == "warning"
        assert result.warning_format.status == "advisory"
        cpi = next(f for f in result.warning_format.findings if f.code == "characters_per_inch")
        assert cpi.status == "advisory"
        assert cpi.estimated is True
        assert cpi.citation == "27 CFR 16.22(a)(4)"
        assert "32.4" in cpi.message
        assert "25" in cpi.message

    def test_calibrated_type_size_fail_is_format_fail(self):
        ts = TypeSizeMeasurement(
            method=CalibrationMethod.SCALE_MARKER_CARD_ID1,
            pixels_per_mm=10.0,
            measured_capital_height_mm=1.3,
            required_min_height_mm=2.0,
            max_characters_per_inch=25,
            state="fail",
            verification_note="Calibrated capital letter height 1.30 mm is below 2.0 mm requirement.",
        )
        result = _gw(_extracted(), type_size_details=ts)
        assert result.status == "fail"
        assert result.warning_format.status == "fail"
        type_size = next(f for f in result.warning_format.findings if f.code == "type_size")
        assert type_size.status == "fail"
        assert type_size.citation == "27 CFR 16.22(b)"

    def test_uncalibrated_keeps_field_pass_and_format_advisory(self):
        ts = TypeSizeMeasurement(
            method=CalibrationMethod.UNCALIBRATED_ESTIMATE,
            required_min_height_mm=2.0,
            max_characters_per_inch=25,
            state="warning",
            verification_note="Uncalibrated photo. Physical gauge measurement required.",
        )
        result = _gw(_extracted(), type_size_details=ts)
        assert result.status == "pass"
        assert result.warning_format.status == "advisory"
        assert any(f.code == "type_size" and f.status == "advisory" for f in result.warning_format.findings)


def test_application_review_short_form_fails():
    results = run_compliance_checks(
        _application(net_contents="50 mL"),
        _extracted(net_contents="50 mL", government_warning_body=SHORT_FORM_BODY),
    )
    gw = next(r for r in results if r.field == "government_warning")
    assert gw.status == "fail"
    assert gw.warning_text_diff is not None
    assert not gw.warning_text_diff.exact_match
