"""Tests for the demo auth gate (app.auth) and image/MIME validation (app.main).

These complement test_compliance.py (which unit-tests the compliance engine).
Here we cover two cross-cutting concerns added to the dev environment:

1. The demo access gate (``require_demo_access``):
   - fail-open when DEMO_ACCESS_TOKEN is unset (dev/eval never locked out)
   - 401 on missing / malformed / wrong bearer token when a token IS set
   - success when the correct bearer token is supplied
   - unprotected endpoints stay open and never leak the token

2. Content-based image validation (``_reject_if_not_image`` & friends):
   - real image magic bytes (JPEG/PNG/GIF/BMP/TIFF/WEBP/HEIC) are accepted
   - spoofed / renamed non-images are rejected regardless of extension
   - oversize and too-many-images limits are enforced

All env manipulation uses monkeypatch so tests are isolated and order-independent;
auth reads the token dynamically per-request, so no module reload is required.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app import auth
from app import main as main_module
from app.main import (
    MAX_FILE_SIZE,
    MAX_IMAGES_PER_LABEL,
    _reject_if_not_image,
    app,
)

TOKEN_ENV = "DEMO_ACCESS_TOKEN"
USERNAME_ENV = "DEMO_USERNAME"

# Minimal valid ApplicationData payload (required Form field on /api/review).
import json
VALID_APPLICATION = json.dumps({
    "beverage_type": "distilled_spirits",
    "brand_name": "Old Tom Distillery",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
})


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def client() -> TestClient:
    """A TestClient bound to the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def no_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the gate is DISABLED (fail-open) by clearing the token env var."""
    monkeypatch.delenv(TOKEN_ENV, raising=False)


@pytest.fixture
def with_auth(monkeypatch: pytest.MonkeyPatch) -> str:
    """Enable the gate with a known token and return that token."""
    token = "s3cret-demo-token"
    monkeypatch.setenv(TOKEN_ENV, token)
    return token


def _png_bytes(width: int = 8, height: int = 8) -> bytes:
    """Return the bytes of a tiny valid PNG image via Pillow."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 40)).save(buf, format="PNG")
    return buf.getvalue()


# Minimal but valid container signatures for each accepted format. These are
# real magic-byte prefixes; _reject_if_not_image checks the leading bytes only.
JPEG_SIG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 16
PNG_SIG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
GIF_SIG = b"GIF89a" + b"\x00" * 16
BMP_SIG = b"BM" + b"\x00" * 20
TIFF_LE_SIG = b"II*\x00" + b"\x00" * 16
TIFF_BE_SIG = b"MM\x00*" + b"\x00" * 16
WEBP_SIG = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16
HEIC_SIG = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16
HEIF_SIG = b"\x00\x00\x00\x18ftypmif1" + b"\x00" * 16


# ===========================================================================
# Auth gate: unit-level behaviour of the helpers
# ===========================================================================
def test_access_gate_env_name_matches_documented_variable() -> None:
    """The gate still reads DEMO_ACCESS_TOKEN; the name is composed, not a secret."""
    assert auth._TOKEN_ENV == TOKEN_ENV


def test_auth_disabled_when_token_unset(no_auth: None) -> None:
    assert auth.auth_enabled() is False


def test_auth_enabled_when_token_set(with_auth: str) -> None:
    assert auth.auth_enabled() is True


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Bearer abc123", "abc123"),
        ("bearer abc123", "abc123"),          # scheme is case-insensitive
        ("Bearer   spaced  ", "spaced"),        # token is stripped
        (None, None),
        ("", None),
        ("Token abc123", None),                 # wrong scheme
        ("Bearer", None),                       # missing token part
        ("abc123", None),                       # no scheme
    ],
)
def test_extract_bearer(header, expected) -> None:
    assert auth._extract_bearer(header) == expected


# ===========================================================================
# Auth gate: HTTP-level behaviour on a protected endpoint (/api/review)
# ===========================================================================
def _post_review(
    client: TestClient,
    token: str | None = None,
    files=None,
):
    """POST to the protected /api/review endpoint with a valid application field.

    Defaults to a single valid PNG unless *files* is supplied, so auth tests
    exercise the gate rather than tripping upload validation first.
    """
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    if files is None:
        files = [("files", ("label.png", _png_bytes(), "image/png"))]
    data = {"application": VALID_APPLICATION}
    return client.post("/api/review", files=files, data=data, headers=headers)


def test_protected_endpoint_open_when_auth_disabled(
    client: TestClient, no_auth: None
) -> None:
    """Fail-open: with no token configured, no Authorization header is needed.

    The request must get PAST the auth gate (i.e. not a 401). Downstream it may
    fail for other reasons (e.g. the Claude client), but never on auth.
    """
    resp = _post_review(client)
    assert resp.status_code != 401


def test_protected_endpoint_rejects_missing_token(
    client: TestClient, with_auth: str
) -> None:
    resp = _post_review(client)  # no Authorization header
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


def test_protected_endpoint_rejects_wrong_token(
    client: TestClient, with_auth: str
) -> None:
    resp = _post_review(client, token="not-the-real-token")
    assert resp.status_code == 401


def test_protected_endpoint_accepts_correct_token(
    client: TestClient, with_auth: str
) -> None:
    """Correct token must clear the gate (status is not the 401 auth failure)."""
    resp = _post_review(client, token=with_auth)
    assert resp.status_code != 401


# ===========================================================================
# Image / MIME validation: _reject_if_not_image (content-based, not extension)
# ===========================================================================
@pytest.mark.parametrize(
    "name,data",
    [
        ("jpeg", JPEG_SIG),
        ("png", PNG_SIG),
        ("gif", GIF_SIG),
        ("bmp", BMP_SIG),
        ("tiff_le", TIFF_LE_SIG),
        ("tiff_be", TIFF_BE_SIG),
        ("webp", WEBP_SIG),
        ("heic", HEIC_SIG),
        ("heif", HEIF_SIG),
    ],
)
def test_accepts_real_image_signatures(name: str, data: bytes) -> None:
    """Every supported container signature must be accepted (returns None)."""
    assert _reject_if_not_image(data, f"label.{name}") is None


def test_accepts_pillow_generated_png() -> None:
    assert _reject_if_not_image(_png_bytes(), "label.png") is None


@pytest.mark.parametrize(
    "filename,data",
    [
        # A text file renamed with an image extension must still be rejected -
        # validation is by content, never by the client-supplied extension.
        ("totally_a.jpg", b"This is plain text, not an image at all."),
        ("malware.png", b"MZ\x90\x00\x03" + b"\x00" * 32),   # PE/EXE header
        ("doc.jpeg", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"),        # PDF header
        ("empty.png", b""),                                     # empty upload
        ("almost.webp", b"RIFF\x00\x00\x00\x00AVI "),          # RIFF but not WEBP
        ("clip.mp4", b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8),  # ISO-BMFF but not HEIF
    ],
)
def test_rejects_spoofed_non_images(filename: str, data: bytes) -> None:
    """Renamed / spoofed non-images are rejected with a clear message."""
    err = _reject_if_not_image(data, filename)
    assert err is not None
    assert filename in err                      # message names the offending file
    assert "supported image" in err.lower()
    assert "HEIC" in err


# ===========================================================================
# Image / MIME validation: end-to-end through the protected endpoint
# (auth disabled so we isolate the upload-validation path). The endpoint does
# not 4xx on a bad image; it returns 200 with overall_status="error" (or
# "retake" for photo quality) so downstream clients get a structured result.
# ===========================================================================
def _review_with_files(client: TestClient, files) -> dict:
    resp = client.post(
        "/api/review", files=files, data={"application": VALID_APPLICATION}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_upload_rejects_non_image_file(client: TestClient, no_auth: None) -> None:
    files = [("files", ("notes.jpg", b"just some text", "image/jpeg"))]
    body = _review_with_files(client, files)
    assert body["overall_status"] == "error"
    assert body["error_code"] == "UNSUPPORTED_FILE"
    assert "supported image" in (body["error"] or "").lower()
    assert body.get("request_id")


def test_upload_rejects_oversize_file(client: TestClient, no_auth: None) -> None:
    # One byte over the limit, but with a valid PNG prefix so size is what trips.
    oversize = PNG_SIG + b"\x00" * (MAX_FILE_SIZE + 1)
    files = [("files", ("huge.png", oversize, "image/png"))]
    body = _review_with_files(client, files)
    assert body["overall_status"] == "error"
    assert body["error_code"] == "FILE_TOO_LARGE"
    assert "10 mb" in (body["error"] or "").lower()
    assert body.get("request_id")


def test_upload_rejects_too_many_images(client: TestClient, no_auth: None) -> None:
    png = _png_bytes()
    files = [
        ("files", (f"label{i}.png", png, "image/png"))
        for i in range(MAX_IMAGES_PER_LABEL + 1)
    ]
    body = _review_with_files(client, files)
    assert body["overall_status"] == "error"
    assert body["error_code"] == "INVALID_REQUEST"
    # Either the per-request limit message or the _read_images "maximum" message;
    # both mention "images per label".
    assert "images" in (body["error"] or "").lower()
    assert "per label" in (body["error"] or "").lower()
    assert body.get("request_id")


def test_upload_accepts_valid_image_past_validation(
    client: TestClient, no_auth: None
) -> None:
    """A valid PNG must clear upload validation: no image-related error is set."""
    files = [("files", ("label.png", _png_bytes(), "image/png"))]
    body = _review_with_files(client, files)
    err = (body["error"] or "").lower()
    assert "supported image" not in err
    assert "exceeds" not in err
    assert "maximum" not in err
