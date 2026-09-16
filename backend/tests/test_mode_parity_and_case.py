"""Cross-mode parity + government-warning case-handling tests.

Single-label review and batch review use run_compliance_checks(); the
label-only check uses check_label_requirements().  Both share the same
check helpers, so they must agree for every input.  These tests also cover
the reported bug where a Government Warning in a non-canonical case was
hard-failed by one mode.
"""
from app.compliance import (
    CANONICAL_WARNING_BODY,
    CANONICAL_WARNING_HEADER,
    check_label_requirements,
    run_compliance_checks,
)
from app.models import ApplicationData, ExtractedLabelData


def _app(**o):
    b = dict(beverage_type="distilled_spirits", brand_name="OLD TOM DISTILLERY",
             class_type="Kentucky Straight Bourbon Whiskey",
             alcohol_content="45% Alc./Vol. (90 Proof)", net_contents="750 mL")
    b.update(o); return ApplicationData(**b)


def _ext(**o):
    b = dict(brand_name="OLD TOM DISTILLERY",
             class_type="Kentucky Straight Bourbon Whiskey",
             alcohol_content="45% Alc./Vol. (90 Proof)", net_contents="750 mL",
             government_warning_present=True,
             government_warning_header=CANONICAL_WARNING_HEADER,
             government_warning_body=CANONICAL_WARNING_BODY)
    b.update(o); return ExtractedLabelData(**b)


def _gw(results):
    return next(r for r in results if r.field == "government_warning")


def _review(h):
    return _gw(run_compliance_checks(_app(), _ext(government_warning_header=h))).status


def _labelonly(h):
    return _gw(check_label_requirements(_ext(government_warning_header=h), beverage_type="distilled_spirits")).status


HEADERS = ["GOVERNMENT WARNING:", "Government Warning:", "government warning:",
           "GOVERNMENT WARNING", "  GOVERNMENT WARNING:  "]


def test_gw_casing_parity_across_modes():
    for h in HEADERS:
        assert _review(h) == _labelonly(h), f"divergence for {h!r}"


def test_gw_uppercase_passes():
    assert _review("GOVERNMENT WARNING:") == "pass"
    assert _labelonly("GOVERNMENT WARNING:") == "pass"


def test_gw_titlecase_is_hard_fail():
    """Issue #6 W03: Non-uppercase header must hard-fail under 27 CFR 16.21/16.22."""
    assert _review("Government Warning:") == "fail"
    assert _labelonly("Government Warning:") == "fail"


def test_gw_lowercase_is_hard_fail():
    """Issue #6 W03: Lowercase header must hard-fail under 27 CFR 16.21/16.22."""
    assert _review("government warning:") == "fail"
    assert _labelonly("government warning:") == "fail"


def test_gw_missing_colon_is_hard_fail():
    """Issue #6 W03: Header missing colon must hard-fail under 27 CFR 16.21/16.22."""
    assert _review("GOVERNMENT WARNING") == "fail"
    assert _labelonly("GOVERNMENT WARNING") == "fail"


def test_gw_wrong_wording_fails_all_modes():
    e = _ext(government_warning_body="Drinking is totally fine, enjoy!")
    assert _gw(run_compliance_checks(_app(), e)).status == "fail"
    assert _gw(check_label_requirements(e, beverage_type="distilled_spirits")).status == "fail"


def test_gw_missing_fails_all_modes():
    e = _ext(government_warning_present=False)
    assert _gw(run_compliance_checks(_app(), e)).status == "fail"
    assert _gw(check_label_requirements(e, beverage_type="distilled_spirits")).status == "fail"


def test_shared_fields_status_parity():
    e = _ext()
    rv = {r.field: r.status for r in run_compliance_checks(_app(), e)}
    lo = {r.field: r.status for r in check_label_requirements(e, beverage_type="distilled_spirits")}
    shared = set(rv) & set(lo)
    assert shared
    for f in shared:
        assert rv[f] == lo[f], f"divergence on {f}"
