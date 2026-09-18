"""Tests for COLA Application pre-fill MVP: CSV/JSON template import & presets.

Covers:
1. Pure unit tests for app.prefill (CSV parse, JSON parse, aliases, beverage normalization, missing fields).
2. API endpoint integration tests for:
   - GET /api/cola/presets (200 OK, valid presets structure)
   - POST /api/cola/parse-template happy path (CSV single & multi-row, JSON single & array)
   - POST /api/cola/parse-template validation errors (HTTP 422 for malformed JSON, bad headers, missing fields, empty files, invalid beverage types)
   - Fail-open / zero-login verification (never returns 401 when demo auth gate is active or inactive)
"""

from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import ApplicationData
from app.prefill import (
    BUILTIN_COLA_PRESETS,
    normalize_beverage_type,
    normalize_header,
    parse_application_csv,
    parse_application_json,
    parse_application_template,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Unit tests: Header and Enum normalization
# ---------------------------------------------------------------------------

def test_normalize_header_aliases():
    assert normalize_header("Brand Name") == "brand_name"
    assert normalize_header("brand") == "brand_name"
    assert normalize_header("Class/Type") == "class_type"
    assert normalize_header("class-type") == "class_type"
    assert normalize_header("ABV") == "alcohol_content"
    assert normalize_header("alc_by_vol") == "alcohol_content"
    assert normalize_header("Net Contents") == "net_contents"
    assert normalize_header("Volume") == "net_contents"
    assert normalize_header("Producer") == "name_and_address"
    assert normalize_header("Bottler") == "name_and_address"
    assert normalize_header("Country of Origin") == "country_of_origin"
    assert normalize_header("Origin") == "country_of_origin"


def test_normalize_beverage_type_valid():
    assert normalize_beverage_type("distilled_spirits") == "distilled_spirits"
    assert normalize_beverage_type("Spirits") == "distilled_spirits"
    assert normalize_beverage_type("distilled spirits") == "distilled_spirits"
    assert normalize_beverage_type("wine") == "wine"
    assert normalize_beverage_type("Cider") == "wine"
    assert normalize_beverage_type("beer") == "beer"
    assert normalize_beverage_type("Malt Beverage") == "beer"
    assert normalize_beverage_type("Ale") == "beer"


def test_normalize_beverage_type_invalid():
    with pytest.raises(ValueError, match="Invalid beverage type"):
        normalize_beverage_type("kombucha")

    with pytest.raises(ValueError, match="Beverage type is required"):
        normalize_beverage_type("")


# ---------------------------------------------------------------------------
# Unit tests: JSON Parser
# ---------------------------------------------------------------------------

def test_parse_application_json_single_object():
    payload = {
        "beverage_type": "distilled_spirits",
        "brand_name": "Old Tom Distillery",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "alcohol_content": "45% Alc./Vol.",
        "net_contents": "750 mL",
        "name_and_address": "Old Tom Distillery, Bardstown, KY",
        "country_of_origin": None,
    }
    apps = parse_application_json(json.dumps(payload))
    assert len(apps) == 1
    assert isinstance(apps[0], ApplicationData)
    assert apps[0].brand_name == "Old Tom Distillery"
    assert apps[0].beverage_type == "distilled_spirits"
    assert apps[0].alcohol_content == "45% Alc./Vol."


def test_parse_application_json_array():
    payload = [
        {
            "beverage_type": "wine",
            "brand_name": "Silver Oak",
            "class_type": "Cabernet Sauvignon",
            "alcohol_content": "14.2%",
            "net_contents": "750 mL",
        },
        {
            "beverage_type": "beer",
            "brand_name": "Hop Valley",
            "class_type": "IPA",
            "alcohol_content": "6.8%",
            "net_contents": "12 FL OZ",
        },
    ]
    apps = parse_application_json(json.dumps(payload))
    assert len(apps) == 2
    assert apps[0].beverage_type == "wine"
    assert apps[1].beverage_type == "beer"


def test_parse_application_json_missing_field():
    payload = {
        "beverage_type": "wine",
        "brand_name": "Silver Oak",
        # missing class_type, alcohol_content, net_contents
    }
    with pytest.raises(ValueError, match="Missing required application field"):
        parse_application_json(json.dumps(payload))


def test_parse_application_json_malformed():
    with pytest.raises(ValueError, match="Malformed JSON syntax"):
        parse_application_json("{ bad json ")


# ---------------------------------------------------------------------------
# Unit tests: CSV Parser
# ---------------------------------------------------------------------------

def test_parse_application_csv_happy_path():
    csv_text = (
        "beverage_type,brand_name,class_type,alcohol_content,net_contents,name_and_address,country_of_origin\n"
        "distilled_spirits,Old Tom,Bourbon Whiskey,45% ABV,750 mL,\"Bardstown, KY\",\n"
        "wine,Napa Estate,Chardonnay,13.5% ABV,750 mL,\"Napa, CA\",\n"
    )
    apps = parse_application_csv(csv_text)
    assert len(apps) == 2
    assert apps[0].brand_name == "Old Tom"
    assert apps[0].name_and_address == "Bardstown, KY"
    assert apps[1].class_type == "Chardonnay"


def test_parse_application_csv_with_aliases_and_crlf():
    csv_text = (
        "Beverage,Brand,Class,ABV,Volume,Producer,Origin\r\n"
        "Spirits,Highland Glen,Scotch,43%,700 mL,\"Glen Co, NY\",Product of Scotland\r\n"
    )
    apps = parse_application_csv(csv_text)
    assert len(apps) == 1
    assert apps[0].beverage_type == "distilled_spirits"
    assert apps[0].brand_name == "Highland Glen"
    assert apps[0].class_type == "Scotch"
    assert apps[0].alcohol_content == "43%"
    assert apps[0].net_contents == "700 mL"
    assert apps[0].name_and_address == "Glen Co, NY"
    assert apps[0].country_of_origin == "Product of Scotland"


def test_parse_application_csv_missing_fields():
    csv_text = (
        "brand_name,class_type\n"
        "Old Tom,Bourbon\n"
    )
    with pytest.raises(ValueError, match="Missing required application field"):
        parse_application_csv(csv_text)


def test_parse_application_csv_empty():
    with pytest.raises(ValueError, match="CSV file is empty"):
        parse_application_csv("")

    with pytest.raises(ValueError, match="CSV file contains headers but has no data rows"):
        parse_application_csv("beverage_type,brand_name,class_type,alcohol_content,net_contents\n")


# ---------------------------------------------------------------------------
# API Integration tests
# ---------------------------------------------------------------------------

def test_api_list_cola_presets(client: TestClient):
    resp = client.get("/api/cola/presets")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 3
    for preset in data:
        assert "id" in preset
        assert "name" in preset
        assert "application" in preset
        app_data = preset["application"]
        assert "beverage_type" in app_data
        assert "brand_name" in app_data
        assert "class_type" in app_data
        assert "alcohol_content" in app_data
        assert "net_contents" in app_data


def test_api_parse_cola_template_csv_happy(client: TestClient):
    csv_content = (
        "beverage_type,brand_name,class_type,alcohol_content,net_contents\n"
        "beer,Test Brewery,Pale Ale,5.5% ABV,12 FL OZ\n"
    )
    files = {"file": ("application.csv", csv_content.encode("utf-8"), "text/csv")}
    resp = client.post("/api/cola/parse-template", files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["beverage_type"] == "beer"
    assert data[0]["brand_name"] == "Test Brewery"
    assert data[0]["class_type"] == "Pale Ale"
    assert data[0]["alcohol_content"] == "5.5% ABV"
    assert data[0]["net_contents"] == "12 FL OZ"


def test_api_parse_cola_template_json_happy(client: TestClient):
    json_content = json.dumps({
        "beverage_type": "wine",
        "brand_name": "Valley Cellars",
        "class_type": "Pinot Noir",
        "alcohol_content": "13.8%",
        "net_contents": "750 mL",
        "name_and_address": "Valley Cellars, Sonoma, CA",
    })
    files = {"file": ("app.json", json_content.encode("utf-8"), "application/json")}
    resp = client.post("/api/cola/parse-template", files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["brand_name"] == "Valley Cellars"
    assert data[0]["beverage_type"] == "wine"


def test_api_parse_cola_template_bad_csv_returns_422(client: TestClient):
    bad_csv = "random_column_1,random_column_2\nfoo,bar\n"
    files = {"file": ("bad.csv", bad_csv.encode("utf-8"), "text/csv")}
    resp = client.post("/api/cola/parse-template", files=files)
    assert resp.status_code == 422
    data = resp.json()
    assert "Missing required application field" in data["detail"]


def test_api_parse_cola_template_bad_json_returns_422(client: TestClient):
    bad_json = "{\"broken_json\": true,"
    files = {"file": ("bad.json", bad_json.encode("utf-8"), "application/json")}
    resp = client.post("/api/cola/parse-template", files=files)
    assert resp.status_code == 422
    data = resp.json()
    assert "Malformed JSON" in data["detail"]


def test_api_parse_cola_template_invalid_beverage_type_returns_422(client: TestClient):
    csv_content = (
        "beverage_type,brand_name,class_type,alcohol_content,net_contents\n"
        "kombucha,GT's,Gingerade,0.5%,16 FL OZ\n"
    )
    files = {"file": ("app.csv", csv_content.encode("utf-8"), "text/csv")}
    resp = client.post("/api/cola/parse-template", files=files)
    assert resp.status_code == 422
    data = resp.json()
    assert "Invalid beverage type 'kombucha'" in data["detail"]


def test_api_parse_cola_template_never_returns_401(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    # Even if DEMO_ACCESS_TOKEN is configured in the environment, the template parse endpoint
    # must remain fail-open / zero-login for evaluators (never return 401).
    monkeypatch.setenv("DEMO_ACCESS_TOKEN", "active-secret-gate-token")
    csv_content = (
        "beverage_type,brand_name,class_type,alcohol_content,net_contents\n"
        "beer,Test Brewery,Pale Ale,5.5% ABV,12 FL OZ\n"
    )
    files = {"file": ("application.csv", csv_content.encode("utf-8"), "text/csv")}
    # No Authorization header provided
    resp = client.post("/api/cola/parse-template", files=files)
    assert resp.status_code == 200
    assert resp.json()[0]["brand_name"] == "Test Brewery"

    # And for bad csv, it should return 422, never 401
    bad_files = {"file": ("bad.csv", b"junk\nfoo\n", "text/csv")}
    bad_resp = client.post("/api/cola/parse-template", files=bad_files)
    assert bad_resp.status_code == 422
