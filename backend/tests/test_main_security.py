import importlib

from fastapi.testclient import TestClient


def _load_main(monkeypatch, api_auth_token: str | None = None):
    if api_auth_token is None:
        monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("API_AUTH_TOKEN", api_auth_token)
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
