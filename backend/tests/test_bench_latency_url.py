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


def test_http_origin_rejects_file_and_empty():
    with pytest.raises(ValueError, match="http"):
        bench_latency.http_origin("file:///etc/passwd")
    with pytest.raises(ValueError, match="http"):
        bench_latency.http_origin("ftp://example.test")
    with pytest.raises(ValueError, match="http"):
        bench_latency.http_origin("")
