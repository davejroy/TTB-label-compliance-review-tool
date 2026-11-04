"""Operator bench script must not follow file:// or other non-HTTP origins."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bench_latency.py"
_SPEC = importlib.util.spec_from_file_location("bench_latency", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
bench_latency = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench_latency)


def test_http_origin_accepts_http_and_https():
    assert bench_latency.http_origin("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    assert bench_latency.http_origin("https://api.example.test/extra") == "https://api.example.test"


def test_redact_auth_covers_scheme_and_json_keys():
    header = "Authorization: Bearer demo-token-value"
    assert "demo-token-value" not in bench_latency._redact_auth(header)
    assert "[REDACTED]" in bench_latency._redact_auth(header)
    body = '{"authorization":"secret-word-token","ok":true}'
    assert "secret-word-token" not in bench_latency._redact_auth(body)


def test_http_origin_rejects_file_and_empty():
    with pytest.raises(ValueError, match="http"):
        bench_latency.http_origin("file:///etc/passwd")
    with pytest.raises(ValueError, match="http"):
        bench_latency.http_origin("ftp://example.test")
    with pytest.raises(ValueError, match="http"):
        bench_latency.http_origin("")
