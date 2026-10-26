"""Tests for tightened CORS allowlist, methods, headers, and credentials.

Verifies the port of -dev 91e88ff:
1. Unset CORS_ORIGINS fails open to local Vite + the render.yaml frontend.
2. Explicit CORS_ORIGINS values are honored (including operator wildcard *).
3. Unknown browser origins do not receive Access-Control-Allow-Origin.
4. Allowed methods/headers are restricted; credentials stay disabled.
5. Review endpoints remain reachable without auth (fail-open review flow).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

import app.main as main_module


_RENDER_FRONTEND = "https://ttb-label-frontend.onrender.com"
_LOCAL_VITE = "http://localhost:5173"


def test_parse_cors_origins_defaults_to_local_and_render_frontend():
    """Empty/unset CORS_ORIGINS keeps the Render frontend and local Vite allowed."""
    expected = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        _RENDER_FRONTEND,
    ]
    assert main_module._parse_cors_origins(None) == expected
    assert main_module._parse_cors_origins("") == expected
    assert main_module._parse_cors_origins("   ") == expected
    assert _RENDER_FRONTEND in main_module._DEFAULT_CORS_ORIGINS
    assert "*" not in main_module._DEFAULT_CORS_ORIGINS


def test_parse_cors_origins_honors_explicit_list_and_wildcard():
    """Operators can lock the allowlist or restore a wildcard explicitly."""
    assert main_module._parse_cors_origins(
        "https://ttb-label-frontend.onrender.com, http://localhost:5173"
    ) == [_RENDER_FRONTEND, _LOCAL_VITE]
    assert main_module._parse_cors_origins("*") == ["*"]
    assert main_module._parse_cors_origins("https://example.test, *") == ["*"]


def test_cors_middleware_uses_tightened_defaults():
    """Installed CORSMiddleware matches the tightened fail-open defaults."""
    cors_kwargs = None
    for middleware in main_module.app.user_middleware:
        if middleware.cls is CORSMiddleware:
            cors_kwargs = middleware.kwargs
            break
    assert cors_kwargs is not None
    assert _RENDER_FRONTEND in cors_kwargs["allow_origins"]
    assert _LOCAL_VITE in cors_kwargs["allow_origins"]
    assert "*" not in cors_kwargs["allow_origins"]
    assert cors_kwargs["allow_methods"] == ["GET", "POST", "OPTIONS"]
    assert cors_kwargs["allow_headers"] == ["Authorization", "Content-Type"]
    assert cors_kwargs["allow_credentials"] is False


def test_cors_allows_render_frontend_and_local_vite_origins():
    """Preflight from the deployed frontend and local Vite is allowed."""
    client = TestClient(main_module.app)
    for origin in (_RENDER_FRONTEND, _LOCAL_VITE, "http://127.0.0.1:5173"):
        res = client.options(
            "/api/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert res.status_code == 200
        assert res.headers["access-control-allow-origin"] == origin
        allow_methods = res.headers.get("access-control-allow-methods", "")
        assert "POST" in allow_methods
        assert "GET" in allow_methods
        assert "DELETE" not in allow_methods
        allow_headers = res.headers.get("access-control-allow-headers", "").lower()
        assert "authorization" in allow_headers
        assert "content-type" in allow_headers
        assert res.headers.get("access-control-allow-credentials") != "true"


def test_cors_rejects_unknown_origin_without_blocking_the_request():
    """Unknown origins get no ACAO header; the review API still answers 200."""
    client = TestClient(main_module.app)
    res = client.get(
        "/api/health",
        headers={"Origin": "https://evil.example"},
    )
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    assert res.headers.get("access-control-allow-origin") != "https://evil.example"


def test_cors_preflight_rejects_disallowed_method():
    """Preflight for methods outside GET/POST/OPTIONS is not allowed."""
    client = TestClient(main_module.app)
    res = client.options(
        "/api/health",
        headers={
            "Origin": _RENDER_FRONTEND,
            "Access-Control-Request-Method": "DELETE",
        },
    )
    assert res.status_code == 400
