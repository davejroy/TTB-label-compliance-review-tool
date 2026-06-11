from app.compliance import (
    CANONICAL_WARNING_BODY,
    CANONICAL_WARNING_HEADER,
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
