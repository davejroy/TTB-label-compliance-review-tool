"""Tests for backend CORS configuration and fail-open behavior.

Verifies:
1. Default allowlist contains known origins (prod frontend + local Vite).
2. CORS uses an exact origin list only — no host regex.
3. Allowed origin gets Access-Control-Allow-Origin echoed on requests and preflight OPTIONS.
4. Disallowed origin does not get Access-Control-Allow-Origin, but request still succeeds (200, not 401/403).
5. Requests with no Origin header (curl/non-browser clients) succeed unaffected (200, no 401/403).
6. Environment variable overrides (ALLOWED_ORIGINS, CORS_ALLOWED_ORIGINS, CORS_ORIGINS) work.
7. Strict priority order: ALLOWED_ORIGINS > CORS_ALLOWED_ORIGINS > CORS_ORIGINS.
8. Origin validation & garbage handling.
9. Explicit wildcard '*' is honored only outside production.
10. A parse/store exception fails open to the default allowlist.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.main import (
    DEFAULT_CORS_ORIGINS,
    _is_valid_cors_origin,
    app as main_app,
    cors_middleware_kwargs,
    get_cors_configuration,
)


def create_test_app(allow_origins: list[str], allow_origin_regex: str | None = None) -> FastAPI:
    """Create an isolated test FastAPI app configured with specified CORS parameters."""
    test_app = FastAPI()
    test_app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_origin_regex=allow_origin_regex,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @test_app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @test_app.post("/api/echo")
    def echo(data: dict) -> dict:
        return data

    return test_app


# ---------------------------------------------------------------------------
# Unit tests for _is_valid_cors_origin helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "http://localhost",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://ttb-label-frontend.onrender.com",
        "https://example.com:8443",
        "http://sub.domain.example.com",
    ],
)
def test_is_valid_cors_origin_valid(origin: str) -> None:
    """Valid CORS origins return True."""
    assert _is_valid_cors_origin(origin) is True


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "   ",
        "not-a-url",
        "ftp://example.com",
        "javascript:alert(1)",
        "https://example.com/path",
        "https://example.com/",
        "https://example.com?foo=bar",
        "https://example.com#section",
        "://missing-scheme",
        "http://",
    ],
)
def test_is_valid_cors_origin_invalid(origin: str) -> None:
    """Invalid CORS origins return False."""
    assert _is_valid_cors_origin(origin) is False


# ---------------------------------------------------------------------------
# Unit tests for get_cors_configuration helper
# ---------------------------------------------------------------------------

def _clear_cors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ALLOWED_ORIGINS", "CORS_ALLOWED_ORIGINS", "CORS_ORIGINS", "ENVIRONMENT", "APP_ENV", "RENDER_SERVICE_NAME"):
        monkeypatch.delenv(name, raising=False)


def test_cors_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no env vars are set, returns default known origins and no regex."""
    _clear_cors_env(monkeypatch)

    origins, regex = get_cors_configuration()
    assert origins == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://ttb-label-frontend.onrender.com",
    ]
    assert regex is None


def test_cors_config_explicit_wildcard_non_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit '*' is honored only outside production, with a warning."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")
    origins, regex = get_cors_configuration()
    assert origins == ["*"]
    assert regex is None


def test_cors_config_backward_compatible_cors_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    """CORS_ORIGINS env var overrides default list when ALLOWED_ORIGINS is unset."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv("CORS_ORIGINS", "https://custom1.com, https://custom2.com")

    origins, regex = get_cors_configuration()
    assert origins == ["https://custom1.com", "https://custom2.com"]
    assert regex is None


def test_cors_config_allowed_origins_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    """ALLOWED_ORIGINS is the preferred override."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://third-party.example.com")

    origins, regex = get_cors_configuration()
    assert origins == ["https://third-party.example.com"]
    assert regex is None


def test_cors_config_priority_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """ALLOWED_ORIGINS takes priority over CORS_ALLOWED_ORIGINS and CORS_ORIGINS."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://primary.example.com")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://secondary.example.com")
    monkeypatch.setenv("CORS_ORIGINS", "https://tertiary.example.com")

    origins, _ = get_cors_configuration()
    assert origins == ["https://primary.example.com"]


def test_cors_config_empty_top_priority_var_does_not_fall_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty top-priority SET var uses the default allowlist and does NOT fall through."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv("ALLOWED_ORIGINS", "   ")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://lower-priority.example.com")
    monkeypatch.setenv("CORS_ORIGINS", "https://lowest-priority.example.com")

    origins, regex = get_cors_configuration()
    assert origins == list(DEFAULT_CORS_ORIGINS)
    assert regex is None
    assert "*" not in origins


def test_cors_config_empty_cors_origins_does_not_fall_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ALLOWED_ORIGINS is unset, an empty CORS_ALLOWED_ORIGINS uses the default allowlist."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "")
    monkeypatch.setenv("CORS_ORIGINS", "https://lowest-priority.example.com")

    origins, regex = get_cors_configuration()
    assert origins == list(DEFAULT_CORS_ORIGINS)
    assert regex is None
    assert "*" not in origins


def test_cors_config_only_commas_uses_default_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env var containing only commas/whitespace uses the default allowlist, never '*'."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv("ALLOWED_ORIGINS", " , ,   ")
    origins, regex = get_cors_configuration()
    assert origins == list(DEFAULT_CORS_ORIGINS)
    assert regex is None
    assert "*" not in origins


def test_cors_config_garbage_only_uses_default_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """When all entries are garbage/invalid, use the default allowlist, never '*'."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv("ALLOWED_ORIGINS", "not-a-valid-url, ftp://bad, https:///missinghost")
    origins, regex = get_cors_configuration()
    assert origins == list(DEFAULT_CORS_ORIGINS)
    assert regex is None
    assert "*" not in origins


def test_cors_config_mixed_valid_and_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mixed valid and garbage entries filters out garbage and keeps only valid origins."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv(
        "ALLOWED_ORIGINS",
        "https://valid1.example.com, not-valid, http://localhost:3000, ftp://bad.com, https://valid2.example.com/path",
    )
    origins, regex = get_cors_configuration()
    assert origins == ["https://valid1.example.com", "http://localhost:3000"]
    assert regex is None


def test_cors_config_list_containing_wildcard_non_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a non-production list contains '*', returns (['*'], None)."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://example.com, *")
    origins, regex = get_cors_configuration()
    assert origins == ["*"]
    assert regex is None


def test_cors_config_unset_falls_through_to_next(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset ALLOWED_ORIGINS allows CORS_ALLOWED_ORIGINS to take effect."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://active-cors.example.com")

    origins, _ = get_cors_configuration()
    assert origins == ["https://active-cors.example.com"]


def test_production_never_uses_wildcard(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Production replaces an explicit '*' with the default allowlist and warns."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")
    with caplog.at_level("WARNING"):
        origins, regex = get_cors_configuration()
    assert origins == list(DEFAULT_CORS_ORIGINS)
    assert regex is None
    assert "*" not in origins
    assert any("not allowed in production" in rec.message for rec in caplog.records)


def test_production_empty_env_uses_allowlist_not_star(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty CORS_ORIGINS on production (the previous dashboard value) is an allowlist."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CORS_ORIGINS", "")
    origins, regex = get_cors_configuration()
    assert origins == list(DEFAULT_CORS_ORIGINS)
    assert regex is None
    assert "*" not in origins


def test_render_prod_service_never_uses_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    """A production Render service name must not honor CORS_ORIGINS=*."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv("RENDER_SERVICE_NAME", "ttb-label-backend")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    origins, regex = get_cors_configuration()
    assert origins == list(DEFAULT_CORS_ORIGINS)
    assert "*" not in origins
    assert regex is None


def test_cors_parse_failure_fails_open_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """A store/parse exception uses the default allowlist so browsers are not locked out."""
    _clear_cors_env(monkeypatch)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://ok.example.com")

    def boom() -> tuple[str | None, str | None]:
        raise RuntimeError("config store failed")

    monkeypatch.setattr("app.main._resolve_cors_env", boom)
    origins, regex = get_cors_configuration()
    assert origins == list(DEFAULT_CORS_ORIGINS)
    assert regex is None
    assert "*" not in origins


# ---------------------------------------------------------------------------
# Integration tests with TestClient
# ---------------------------------------------------------------------------

@pytest.fixture
def default_client() -> TestClient:
    return TestClient(main_app)


@pytest.mark.parametrize(
    "origin",
    [
        "https://ttb-label-frontend.onrender.com",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
)
def test_default_allowed_origins_echo_acao(default_client: TestClient, origin: str) -> None:
    """Allowed origins receive Access-Control-Allow-Origin header matching their origin."""
    response = default_client.get("/api/health", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin


@pytest.mark.parametrize(
    "origin",
    [
        "https://ttb-label-frontend.onrender.com",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
)
def test_default_allowed_origins_preflight_options(default_client: TestClient, origin: str) -> None:
    """Preflight OPTIONS request from allowed origin succeeds with 200 and required CORS headers."""
    response = default_client.options(
        "/api/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin
    assert "GET" in response.headers.get("access-control-allow-methods", "")


def test_disallowed_origin_gets_no_acao_but_is_not_blocked(default_client: TestClient) -> None:
    """Disallowed origin does NOT get ACAO header, but HTTP response remains 200 (never 401/403)."""
    disallowed_origin = "https://unauthorized-evil-site.com"
    response = default_client.get("/api/health", headers={"Origin": disallowed_origin})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    body = response.json()
    assert body["status"] == "ok"
    assert "vision_configured" in body


def test_no_origin_header_client_unaffected(default_client: TestClient) -> None:
    """Non-browser client (curl, no Origin header) works normally with 200 (never 401/403)."""
    response = default_client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "vision_configured" in body
    assert "access-control-allow-origin" not in response.headers


def test_wildcard_cors_app_echoes_wildcard() -> None:
    """When app is configured with allow_origins=['*'], all origins get ACAO: *."""
    wildcard_app = create_test_app(allow_origins=["*"], allow_origin_regex=None)
    client = TestClient(wildcard_app)

    response = client.get("/api/health", headers={"Origin": "https://any-site.com"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "*"


def test_preview_subdomains_are_not_allowed_without_exact_origin(
    default_client: TestClient,
) -> None:
    """Host-wildcard preview origins are no longer accepted (F13)."""
    preview = "https://frontend-preview.example.test"
    response = default_client.get("/api/health", headers={"Origin": preview})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_cors_kwargs_never_pair_wildcard_with_credentials() -> None:
    """Credentials force the explicit frontend allowlist; never ACAO *."""
    kwargs = cors_middleware_kwargs(["*"], None, allow_credentials=True)
    assert kwargs["allow_credentials"] is True
    assert kwargs["allow_origins"] == list(DEFAULT_CORS_ORIGINS)
    assert "*" not in kwargs["allow_origins"]
    assert kwargs["allow_origin_regex"] is None


def test_live_app_never_uses_wildcard_with_credentials() -> None:
    """The mounted CORS stack must not advertise credentials with origin *."""
    cors_specs = [item for item in main_app.user_middleware if item.cls is CORSMiddleware]
    assert cors_specs
    for spec in cors_specs:
        origins = list(spec.kwargs.get("allow_origins") or [])
        credentials = bool(spec.kwargs.get("allow_credentials"))
        if credentials:
            assert "*" not in origins
        if origins == ["*"]:
            assert credentials is False
