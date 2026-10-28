"""Contract tests for the lightweight synthetic QA preset metadata."""

from fastapi.testclient import TestClient

from app.main import app
from app.prefill import ALL_COLA_PRESETS, SYNTHETIC_QA_PRESETS


def test_synthetic_qa_presets_are_small_metadata_only():
    assert len(SYNTHETIC_QA_PRESETS) >= 4
    assert len(ALL_COLA_PRESETS) == len(SYNTHETIC_QA_PRESETS) + 4
    for preset in SYNTHETIC_QA_PRESETS:
        assert preset["category"] == "synthetic_qa"
        assert preset["fixture_id"]
        assert preset["expected_result"]
        assert "application" in preset
        assert "image" not in preset
        assert "image_bytes" not in preset


def test_preset_endpoint_exposes_qa_metadata_without_images():
    response = TestClient(app).get("/api/cola/presets")
    assert response.status_code == 200
    qa = [item for item in response.json() if item.get("category") == "synthetic_qa"]
    assert len(qa) >= 4
    assert all("fixture_id" in item and "image_bytes" not in item for item in qa)
