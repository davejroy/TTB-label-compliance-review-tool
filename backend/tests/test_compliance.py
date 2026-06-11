from app.compliance import (
    CANONICAL_WARNING_BODY,
    CANONICAL_WARNING_HEADER,
    check_label_requirements,
    overall_status,
    run_compliance_checks,
)
from app.models import ApplicationData, ExtractedLabelData


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


def test_government_warning_title_case_header_fails():
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
        make_extracted(name_and_address="Old Tom Distillery, Bardstown, KY"),
        beverage_type="distilled_spirits",
    )
    assert all(r.status == "pass" for r in results if r.field != "country_of_origin")
    # country of origin is conditional - missing is a warning, not a fail, and
    # is the only thing keeping this from an overall "pass".
    coo = next(r for r in results if r.field == "country_of_origin")
    assert coo.status == "warning"
    assert overall_status(results) == "warning"


def test_label_requirements_missing_brand_name_fails():
    results = check_label_requirements(
        make_extracted(brand_name=""), beverage_type="distilled_spirits"
    )
    brand_result = next(r for r in results if r.field == "brand_name")
    assert brand_result.status == "fail"


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
