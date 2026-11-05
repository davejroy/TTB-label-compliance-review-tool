"""Promote dest f9a52d5: sulfite citation follows beverage type."""

from __future__ import annotations

from app.compliance import (
    CANONICAL_WARNING_BODY,
    CANONICAL_WARNING_HEADER,
    _check_sulfite_declaration,
    _sulfite_citation,
    check_label_requirements,
    run_compliance_checks,
)
from app.models import ApplicationData, ExtractedLabelData

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


@pytest.mark.parametrize(
    "beverage_type,citation",
    [
        ("wine", "27 CFR 4.32(e)"),
        ("distilled_spirits", "27 CFR 5.63(c)(7)"),
        ("beer", "27 CFR 7.63(b)(3)"),
        ("malt beverage", "27 CFR 7.63(b)(3)"),
        ("Malt-Beverage", "27 CFR 7.63(b)(3)"),
        ("spirits", "27 CFR 5.63(c)(7)"),
    ],
)
def test_sulfite_citation_follows_beverage_type(beverage_type, citation):
    assert _sulfite_citation(beverage_type) == citation


@pytest.mark.parametrize(
    "beverage_type,citation",
    [
        ("wine", "27 CFR 4.32(e)"),
        ("distilled_spirits", "27 CFR 5.63(c)(7)"),
        ("beer", "27 CFR 7.63(b)(3)"),
    ],
)
def test_missing_sulfite_cites_type_specific_cfr(beverage_type, citation):
    result = _check_sulfite_declaration(
        _extracted(sulfite_declaration=None, beverage_type_guess=beverage_type),
        beverage_type,
    )
    assert result.status == "needs_verification"
    assert citation in result.message
    if citation != "27 CFR 4.32(e)":
        assert "27 CFR 4.32(e)" not in result.message
    assert "wine contains" not in result.message.lower()


@pytest.mark.parametrize("beverage_type", ["wine", "distilled_spirits", "beer"])
def test_present_sulfite_passes_for_each_type(beverage_type):
    result = _check_sulfite_declaration(
        _extracted(
            sulfite_declaration="Contains Sulfites",
            beverage_type_guess=beverage_type,
        ),
        beverage_type,
    )
    assert result.status == "pass"


def test_cola_review_runs_sulfite_only_for_wine():
    wine = run_compliance_checks(
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
    spirits = run_compliance_checks(
        ApplicationData(
            beverage_type="distilled_spirits",
            brand_name="Old Tom",
            class_type="Bourbon Whiskey",
            alcohol_content="45% Alc./Vol.",
            net_contents="750 mL",
        ),
        _extracted(
            brand_name="Old Tom",
            class_type="Bourbon Whiskey",
            alcohol_content="45% Alc./Vol.",
            net_contents="750 mL",
            beverage_type_guess="distilled_spirits",
            sulfite_declaration=None,
        ),
    )
    assert any(item.field == "sulfite_declaration" for item in wine)
    assert all(item.field != "sulfite_declaration" for item in spirits)
    sulfite = next(item for item in wine if item.field == "sulfite_declaration")
    assert sulfite.status == "needs_verification"
    assert "27 CFR 4.32(e)" in sulfite.message


@pytest.mark.parametrize(
    "beverage_type,citation",
    [
        ("wine", "27 CFR 4.32(e)"),
        ("distilled_spirits", "27 CFR 5.63(c)(7)"),
        ("beer", "27 CFR 7.63(b)(3)"),
    ],
)
def test_label_only_missing_sulfite_uses_confirmed_type(beverage_type, citation):
    results = check_label_requirements(
        _extracted(sulfite_declaration=None, beverage_type_guess=beverage_type),
        beverage_type=beverage_type,
    )
    sulfite = next(item for item in results if item.field == "sulfite_declaration")
    assert sulfite.status == "needs_verification"
    assert citation in sulfite.message
