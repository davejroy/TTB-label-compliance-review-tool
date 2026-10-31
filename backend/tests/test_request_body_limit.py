"""P1 request-body cap: actual-byte enforcement and CORS on HTTP 413.

Covers chunked / missing Content-Length, understated Content-Length, bodies
at or just under the cap, CORS headers on 413 for allowed vs disallowed
origins, preflight OPTIONS, and that single-image plus batch/stream uploads
still succeed when under the limit.
"""

from __future__ import annotations

import asyncio
import io
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.types import Message, Receive, Scope, Send

from app.main import DEFAULT_CORS_ORIGINS, app
from app.models import ExtractedLabelData
from app.request_limits import FILE_TOO_LARGE_MESSAGE, RequestBodyLimitMiddleware
from app.security import API_CSP, HSTS_VALUE

VALID_APPLICATION = {
    "beverage_type": "distilled_spirits",
    "brand_name": "Old Tom Distillery",
    "class_type": "Bourbon",
    "alcohol_content": "45% ABV",
    "net_contents": "750 mL",
}

ALLOWED_ORIGIN = DEFAULT_CORS_ORIGINS[2]  # http://localhost:5173
DISALLOWED_ORIGIN = "https://unauthorized-evil-site.com"


def _png(width: int = 160, height: int = 160) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (80, 120, 160)).save(buf, format="PNG")
    return buf.getvalue()


def _multipart_chunks(
    *,
    extra_bytes: int,
    chunk_size: int = 20,
) -> tuple[list[tuple[bytes, bytes]], list[bytes]]:
    """Build a multipart POST body split into chunks (no Content-Length)."""
    boundary = "----TtbLimitTestBoundary"
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="big.bin"\r\n'
        "Content-Type: application/octet-stream\r\n"
        "\r\n"
    ).encode("ascii")
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
    body = prefix + (b"x" * extra_bytes) + suffix
    chunks = [body[i : i + chunk_size] for i in range(0, len(body), chunk_size)]
    headers = [
        (b"content-type", f"multipart/form-data; boundary={boundary}".encode("ascii")),
    ]
    return headers, chunks


def _extraction() -> ExtractedLabelData:
    return ExtractedLabelData(
        brand_name="Old Tom Distillery",
        class_type="Bourbon",
        alcohol_content="45% ABV",
        net_contents="750 mL",
        government_warning_present=True,
        government_warning_header="GOVERNMENT WARNING:",
        government_warning_body="According to the Surgeon General...",
        extraction_confidence=0.95,
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


async def _asgi_request(
    asgi_app,
    *,
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]],
    body_chunks: list[bytes],
) -> tuple[int, dict[str, str], bytes]:
    """Drive the ASGI app with explicit body chunks (no Content-Length unless set)."""
    remaining = list(body_chunks)

    async def receive() -> Message:
        if remaining:
            body = remaining.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(remaining)}
        return {"type": "http.request", "body": b"", "more_body": False}

    status = 0
    raw_headers: list[tuple[bytes, bytes]] = []
    parts: list[bytes] = []

    async def send(message: Message) -> None:
        nonlocal status, raw_headers
        if message["type"] == "http.response.start":
            status = int(message["status"])
            raw_headers = list(message.get("headers") or [])
        elif message["type"] == "http.response.body":
            parts.append(message.get("body") or b"")

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    await asgi_app(scope, receive, send)
    header_map = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in raw_headers
    }
    return status, header_map, b"".join(parts)


def _assert_413(status: int, body: bytes) -> dict:
    assert status == 413
    payload = json.loads(body.decode("utf-8"))
    assert payload["error_code"] == "FILE_TOO_LARGE"
    assert payload["message"] == FILE_TOO_LARGE_MESSAGE
    assert payload["detail"] == FILE_TOO_LARGE_MESSAGE
    assert payload["request_id"]
    return payload


@pytest.mark.asyncio
async def test_chunked_body_over_limit_without_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transfer-Encoding: chunked (no Content-Length) still hits 413 on actual bytes."""
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "50")
    headers, chunks = _multipart_chunks(extra_bytes=80)
    headers.append((b"transfer-encoding", b"chunked"))
    status, response_headers, body = await _asgi_request(
        app,
        method="POST",
        path="/api/review",
        headers=headers,
        body_chunks=chunks,
    )
    _assert_413(status, body)
    assert response_headers.get("content-type", "").startswith("application/json")


@pytest.mark.asyncio
async def test_understated_content_length_still_413(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content-Length under the cap cannot hide a larger streamed body."""
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "50")
    headers, chunks = _multipart_chunks(extra_bytes=80)
    headers.append((b"content-length", b"20"))
    status, _headers, body = await _asgi_request(
        app,
        method="POST",
        path="/api/review",
        headers=headers,
        body_chunks=chunks,
    )
    _assert_413(status, body)


@pytest.mark.asyncio
async def test_body_at_limit_is_not_413(monkeypatch: pytest.MonkeyPatch) -> None:
    """A body of exactly MAX_REQUEST_BODY_BYTES is accepted by the size guard."""
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "64")
    status, _headers, body = await _asgi_request(
        app,
        method="POST",
        path="/api/review",
        headers=[(b"content-type", b"application/octet-stream")],
        body_chunks=[b"z" * 64],
    )
    assert status != 413
    payload = json.loads(body.decode("utf-8"))
    assert payload.get("error_code") != "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_body_just_under_limit_is_not_413(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "64")
    status, _headers, body = await _asgi_request(
        app,
        method="POST",
        path="/api/review",
        headers=[(b"content-type", b"application/octet-stream")],
        body_chunks=[b"z" * 63],
    )
    assert status != 413
    payload = json.loads(body.decode("utf-8"))
    assert payload.get("error_code") != "FILE_TOO_LARGE"


def test_413_allowed_origin_has_cors_headers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "200")
    resp = client.post(
        "/api/review",
        files=[("files", ("front.png", _png(), "image/png"))],
        data={"application": json.dumps(VALID_APPLICATION)},
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert resp.status_code == 413
    body = resp.json()
    assert body["error_code"] == "FILE_TOO_LARGE"
    assert body["message"] == FILE_TOO_LARGE_MESSAGE
    assert resp.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
    assert resp.headers.get("content-security-policy") == API_CSP


def test_413_disallowed_origin_has_no_cors_headers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "200")
    resp = client.post(
        "/api/review",
        files=[("files", ("front.png", _png(), "image/png"))],
        data={"application": json.dumps(VALID_APPLICATION)},
        headers={"Origin": DISALLOWED_ORIGIN},
    )
    assert resp.status_code == 413
    assert resp.json()["error_code"] == "FILE_TOO_LARGE"
    assert "access-control-allow-origin" not in resp.headers


def test_preflight_options_still_works(client: TestClient) -> None:
    resp = client.options(
        "/api/review",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    assert "POST" in allow_methods
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("content-security-policy") == API_CSP
    assert resp.headers.get("strict-transport-security") is None


def test_preflight_https_includes_hsts(client: TestClient) -> None:
    resp = client.options(
        "/api/review",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
            "X-Forwarded-Proto": "https",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
    assert resp.headers.get("strict-transport-security") == HSTS_VALUE
    assert resp.headers.get("content-security-policy") == API_CSP


def test_single_image_upload_still_works_under_limit(client: TestClient) -> None:
    with patch("app.main.extract_label_fields", return_value=_extraction()):
        resp = client.post(
            "/api/review",
            files=[("files", ("label.png", _png(), "image/png"))],
            data={"application": json.dumps(VALID_APPLICATION)},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall_status"] in {"pass", "warning", "fail"}
    assert body.get("error_code") is None


def test_batch_and_stream_uploads_still_work_under_limit(client: TestClient) -> None:
    files = [("files", ("label.png", _png(), "image/png"))]
    data = {
        "image_counts": "[1]",
        "applications": json.dumps([VALID_APPLICATION]),
    }
    with patch("app.main.extract_label_fields", return_value=_extraction()):
        batch = client.post("/api/review/batch", files=files, data=data)
    assert batch.status_code == 200
    assert isinstance(batch.json(), list)
    assert batch.json()[0]["overall_status"] in {"pass", "warning", "fail"}

    with patch("app.main.extract_label_fields", return_value=_extraction()):
        stream = client.post("/api/review/batch/stream", files=files, data=data)
    assert stream.status_code == 200
    assert "ndjson" in (stream.headers.get("content-type") or "")
    lines = [json.loads(line) for line in stream.text.strip().split("\n") if line.strip()]
    assert any(item.get("done") for item in lines)
    assert any("result" in item for item in lines)


@pytest.mark.asyncio
async def test_byte_counter_does_not_buffer_the_whole_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The receive wrapper forwards each chunk; it does not accumulate a full copy."""
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "50")
    seen_chunks: list[int] = []
    forwarded: list[int] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            forwarded.append(len(message.get("body") or b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    limited = RequestBodyLimitMiddleware(inner)
    chunks = [b"a" * 20, b"b" * 20, b"c" * 20]

    async def receive() -> Message:
        if chunks:
            body = chunks.pop(0)
            seen_chunks.append(len(body))
            return {"type": "http.request", "body": body, "more_body": bool(chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}

    messages: list[Message] = []

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/review",
        "raw_path": b"/api/review",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    await limited(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    assert start["status"] == 413
    # The app saw only the chunks that still fit; the oversize chunk was not forwarded.
    assert forwarded == [20, 20]
    assert seen_chunks[0] == 20


@pytest.mark.asyncio
async def test_413_does_not_wait_for_remaining_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 413 is sent as soon as the cap is crossed; leftover chunks are not drained."""
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "50")
    receive_calls = {"n": 0}

    async def hanging_receive() -> Message:
        receive_calls["n"] += 1
        if receive_calls["n"] == 1:
            return {"type": "http.request", "body": b"x" * 80, "more_body": True}
        await asyncio.Event().wait()
        return {"type": "http.request", "body": b"", "more_body": False}

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        message = await receive()
        if message.get("type") != "http.disconnect":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

    limited = RequestBodyLimitMiddleware(inner)
    messages: list[Message] = []

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/review",
        "raw_path": b"/api/review",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    await asyncio.wait_for(limited(scope, hanging_receive, send), timeout=0.5)
    start = next(m for m in messages if m["type"] == "http.response.start")
    assert start["status"] == 413
    assert receive_calls["n"] == 1


@pytest.mark.asyncio
async def test_limiter_fails_closed_when_cap_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken cap lookup must reject the body, not accept it unlimited."""

    def boom() -> int:
        raise RuntimeError("config store failed")

    monkeypatch.setattr("app.request_limits.get_max_request_body_bytes", boom)
    status, _headers, body = await _asgi_request(
        app,
        method="POST",
        path="/api/review",
        headers=[(b"content-type", b"application/octet-stream")],
        body_chunks=[b"x" * 10],
    )
    _assert_413(status, body)


@pytest.mark.asyncio
async def test_health_is_never_blocked_when_cap_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> int:
        raise RuntimeError("config store failed")

    monkeypatch.setattr("app.request_limits.get_max_request_body_bytes", boom)
    status, _headers, body = await _asgi_request(
        app,
        method="GET",
        path="/api/health",
        headers=[],
        body_chunks=[],
    )
    assert status == 200
    assert json.loads(body.decode("utf-8"))["status"] == "ok"
