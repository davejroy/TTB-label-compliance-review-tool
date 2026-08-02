import importlib

import pytest
from fastapi.testclient import TestClient


def _load_main(monkeypatch, api_auth_token: str | None = None, app_env: str | None = None):
    if api_auth_token is None:
        monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("API_AUTH_TOKEN", api_auth_token)
    if app_env is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", app_env)
    import app.main as main_module

    return importlib.reload(main_module)


def test_health_includes_security_headers(monkeypatch):
    main_module = _load_main(monkeypatch)
    client = TestClient(main_module.app)

    res = client.get("/api/health")

    assert res.status_code == 200
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in res.headers["content-security-policy"]


def test_api_auth_token_restricts_non_health_routes(monkeypatch):
    main_module = _load_main(monkeypatch, api_auth_token="test-token")
    client = TestClient(main_module.app)

    unauthorized = client.post("/api/review")
    assert unauthorized.status_code == 401
    assert unauthorized.json() == {"detail": "Unauthorized"}

    allowed = client.get("/api/health")
    assert allowed.status_code == 200


def test_label_check_batch_rejects_non_positive_image_counts(monkeypatch):
    main_module = _load_main(monkeypatch)
    client = TestClient(main_module.app)

    files = [("files", ("label.jpg", b"fake-image", "image/jpeg"))]
    res = client.post(
        "/api/label-check/batch",
        data={"image_counts": "[-1]"},
        files=files,
    )

    assert res.status_code == 400
    assert (
        res.json()["detail"]
        == "image_counts must be a JSON array of positive integers."
    )


def test_production_without_token_fails_startup(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    import app.main as main_module

    with pytest.raises(RuntimeError, match="API_AUTH_TOKEN must be set"):
        importlib.reload(main_module)


def test_production_with_token_starts_successfully(monkeypatch):
    main_module = _load_main(monkeypatch, api_auth_token="prod-secret", app_env="production")
    client = TestClient(main_module.app)

    res = client.get("/api/health")
    assert res.status_code == 200


def test_non_production_without_token_starts_successfully(monkeypatch):
    main_module = _load_main(monkeypatch, api_auth_token=None, app_env=None)
    client = TestClient(main_module.app)

    res = client.get("/api/health")
    assert res.status_code == 200
