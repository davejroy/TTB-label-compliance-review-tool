"""Offline tests for the regression scorer (no live API)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from score_results import (  # noqa: E402
    _has_usable_report,
    _outcome,
    _type_size_matches,
    _type_size_state,
)


def test_empty_pass_report_is_not_a_pass():
    assert _has_usable_report({}) is False
    assert _outcome({"overall_status": "pass", "fields": [], "extracted": None}) == "error"
    assert _outcome({"overall_status": "pass", "fields": [], "extracted": {}}) == "error"
    assert _outcome({"overall_status": "error"}) == "error"


def test_real_pass_report_still_scores_as_pass():
    body = {
        "overall_status": "pass",
        "fields": [{"field": "brand_name", "status": "pass"}],
        "extracted": {"brand_name": "IRON HOLLOW"},
    }
    assert _has_usable_report(body) is True
    assert _outcome(body) == "pass"


def test_type_size_state_from_details_and_warning_format():
    body = {
        "fields": [
            {
                "field": "government_warning",
                "status": "fail",
                "type_size_details": {"state": "fail", "required_min_height_mm": 2.0},
            }
        ]
    }
    assert _type_size_state(body) == "fail"
    assert _type_size_matches("fail", "fail", {"government_warning": "fail"}) is True
    assert _type_size_matches("pass", "fail", {}) is False
    fmt_only = {
        "fields": [
            {
                "field": "government_warning",
                "warning_format": {
                    "findings": [{"code": "type_size", "status": "fail"}],
                },
            }
        ]
    }
    assert _type_size_state(fmt_only) == "fail"
