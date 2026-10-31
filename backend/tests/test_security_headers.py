"""Security header middleware: present on JSON and streaming routes, fail-open."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
import io

from app.main import app
from app.models import ExtractedLabelData
from app.security import SecurityHeadersMiddleware, request_is_https


def _sample_png(width: int = 150, height: int = 150) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (100, 150, 200)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_security_headers_on_health(client: TestClient):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("content-security-policy") == "default-src 'none'; frame-ancestors 'none'"
    assert "camera=(self)" in (resp.headers.get("permissions-policy") or "")
    assert resp.headers.get("strict-transport-security") is None


def test_hsts_only_when_https(client: TestClient):
    resp = client.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert resp.status_code == 200
    assert resp.headers.get("strict-transport-security") == "max-age=31536000; includeSubDomains"


def test_security_headers_do_not_break_stream(client: TestClient):
    fake = ExtractedLabelData(
        brand_name="Old Tom Distillery",
        class_type="Bourbon",
        alcohol_content="45% ABV",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General...",
        extraction_confidence=0.95,
    )
    with patch("app.main.extract_label_fields", return_value=fake):
        resp = client.post(
            "/api/review/batch/stream",
            files=[("files", ("label.png", _sample_png(), "image/png"))],
            data={
                "image_counts": "[1]",
                "applications": json.dumps([{
                    "beverage_type": "distilled_spirits",
                    "brand_name": "Test",
                    "class_type": "Bourbon",
                    "alcohol_content": "40% ABV",
                    "net_contents": "750 mL",
                }]),
            },
        )
    assert resp.status_code == 200
    assert "ndjson" in (resp.headers.get("content-type") or "")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    lines = [json.loads(line) for line in resp.text.strip().split("\n") if line.strip()]
    assert any("result" in item or item.get("done") for item in lines)


def test_security_headers_middleware_fail_open():
    """A header-write failure must still deliver the downstream response."""

    class BoomApp:
        async def __call__(self, scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": b'{"ok":true}'})

    middleware = SecurityHeadersMiddleware(BoomApp())

    async def broken_send(message):
        raise RuntimeError("cannot write headers")

    # The wrapper itself must not raise when applying headers; send errors
    # from the transport are unrelated. Patch MutableHeaders to explode.
    sent: list[dict] = []

    async def capture(message):
        sent.append(message)

    async def run():
        with patch("app.security.MutableHeaders", side_effect=RuntimeError("header map failed")):
            await middleware(
                {"type": "http", "scheme": "http", "headers": []},
                lambda: None,
                capture,
            )

    import asyncio
    asyncio.run(run())
    assert sent
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 200


def test_request_is_https_from_forwarded_proto():
    scope = {
        "scheme": "http",
        "headers": [(b"x-forwarded-proto", b"https")],
    }
    assert request_is_https(scope) is True
    assert request_is_https({"scheme": "http", "headers": []}) is False
