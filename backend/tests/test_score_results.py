"""Unit tests for the regression scorer (empty mapping and typesize/edge)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "regression"))

import score_results  # noqa: E402


def test_empty_results_mapping_is_rejected():
    with pytest.raises(ValueError, match="empty results mapping"):
        score_results._load_results({"results": {}})
    with pytest.raises(ValueError, match="empty results mapping"):
        score_results._load_results({})
    with pytest.raises(ValueError, match="results payload must be an object"):
        score_results._load_results([])


def test_empty_results_file_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("[]", encoding="utf-8")
    results = tmp_path / "review.json"
    results.write_text(json.dumps({"results": {}}), encoding="utf-8")
    monkeypatch.setattr(score_results, "MANIFEST", manifest)
    monkeypatch.setenv("TTB_REGRESSION_RESULTS", str(results))
    assert score_results.main() == 2


def test_http_500_fails_typesize_and_edge_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {"id": "ts1", "group": "typesize", "ts_expected": "pass"},
                {"id": "ed1", "group": "edge", "expected": "reject"},
            ]
        ),
        encoding="utf-8",
    )
    results = tmp_path / "review.json"
    results.write_text(
        json.dumps(
            {
                "results": {
                    "ts1": {"status": 500, "body": {"overall_status": "error"}},
                    "ed1": {"status": 500, "body": {"overall_status": "error"}},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(score_results, "MANIFEST", manifest)
    monkeypatch.setenv("TTB_REGRESSION_RESULTS", str(results))
    assert score_results.main() == 1


def test_typesize_and_edge_pass_when_outcomes_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {"id": "ts1", "group": "typesize", "ts_expected": "fail"},
                {"id": "ed1", "group": "edge", "expected": "reject"},
            ]
        ),
        encoding="utf-8",
    )
    results = tmp_path / "review.json"
    results.write_text(
        json.dumps(
            {
                "results": {
                    "ts1": {
                        "status": 200,
                        "body": {
                            "overall_status": "fail",
                            "fields": [
                                {
                                    "field": "government_warning",
                                    "status": "fail",
                                    "type_size_details": {"state": "fail"},
                                }
                            ],
                        },
                    },
                    "ed1": {
                        "status": 422,
                        "body": {"overall_status": "retake"},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(score_results, "MANIFEST", manifest)
    monkeypatch.setenv("TTB_REGRESSION_RESULTS", str(results))
    assert score_results.main() == 0
