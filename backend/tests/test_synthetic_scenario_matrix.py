"""Functional and visual QA for the synthetic scenario matrix.

These tests do not call Claude or any external service. The compliance tests
use structured ground truth and the same app functions invoked by the API.
The image tests exercise the deterministic readability gate against generated
photographs with controlled defects.
"""
import sys
from pathlib import Path

import pytest
from PIL import Image

from app.claude_client import diagnose_image_quality

FIXTURE_DIR = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(FIXTURE_DIR))

from synthetic_scenario_matrix import SCENARIOS, build_all, evaluate_scenario  # noqa: E402


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    return build_all(tmp_path_factory.mktemp("synthetic-labels"))


@pytest.mark.parametrize(
    "scenario",
    [item for item in SCENARIOS if item["kind"] in ("label_only", "application_review")],
    ids=lambda item: item["id"],
)
def test_compliance_matrix_matches_ground_truth(scenario):
    statuses = evaluate_scenario(scenario)
    for field, expected in scenario["expected"].items():
        assert statuses[field] == expected, (
            f"{scenario['id']} field {field}: expected {expected}, got {statuses[field]}"
        )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item["id"])
def test_every_scenario_renders_a_decodable_image(scenario, rendered):
    path = rendered[scenario["id"]]
    with Image.open(path) as image:
        image.verify()
    assert path.stat().st_size > 10_000


@pytest.mark.parametrize(
    "scenario",
    [
        item for item in SCENARIOS
        if item["kind"] == "quality" and item["expected"]["quality"] == "readable"
    ],
    ids=lambda item: item["id"],
)
def test_angle_rotation_and_curvature_are_not_automatically_rejected(scenario, rendered):
    result = diagnose_image_quality(Image.open(rendered[scenario["id"]]).convert("RGB"))
    assert result["ok"] is True, f"{scenario['id']} unexpectedly failed: {result}"


def test_severe_blur_requests_a_focus_retake(rendered):
    scenario = next(item for item in SCENARIOS if item["id"] == "quality_severe_blur")
    result = diagnose_image_quality(Image.open(rendered[scenario["id"]]).convert("RGB"))
    assert result["ok"] is False
    assert result["cause"] == "focus"
    assert "retake" in result["message"].lower()


def test_glare_is_not_silently_treated_as_clean_input(rendered):
    scenario = next(item for item in SCENARIOS if item["id"] == "quality_mild_glare")
    result = diagnose_image_quality(Image.open(rendered[scenario["id"]]).convert("RGB"))
    assert result["ok"] is False
    assert result["cause"] in {"glare", "lighting_bright"}
    assert "retake" in result["message"].lower()


def test_matrix_contains_compliant_noncompliant_and_capture_conditions():
    kinds = {item["kind"] for item in SCENARIOS}
    transforms = {item["image_transform"] for item in SCENARIOS}
    assert {"label_only", "application_review", "quality"} <= kinds
    assert {
        "perspective", "rotate", "curved", "glare", "blur", "occlusion", "low_coverage"
    } <= transforms
