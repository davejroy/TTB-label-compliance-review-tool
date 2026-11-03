"""Photo quality pre-gate, EXIF intake, HEIC, type-size source, image_counts.

No live vision calls: extraction is mocked. Extreme short-circuits must not
reach the mock at all.
"""

from __future__ import annotations

import io
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

import app.main as main_module
from app.claude_client import open_image_for_intake, prepare_image
from app.compliance import CANONICAL_WARNING_BODY, CANONICAL_WARNING_HEADER
from app.errors import RETAKE_MESSAGE
from app.limiter import daily_spend_guard, limiter
from app.models import ExtractedLabelData, TypeSizeMeasurement
from app.quality import assess_image_quality

HERE = os.path.dirname(__file__)
QUAL = os.path.join(HERE, "fixtures", "labels", "quality")


def _application() -> dict:
    return {
        "beverage_type": "distilled_spirits",
        "brand_name": "BOURBON CO",
        "class_type": "Whiskey",
        "alcohol_content": "45% ABV",
        "net_contents": "750 mL",
    }


def _readable_extraction(**overrides) -> ExtractedLabelData:
    data = dict(
        brand_name="BOURBON CO",
        class_type="Whiskey",
        alcohol_content="45% ABV",
        net_contents="750 mL",
        name_and_address="123 Main St, Louisville, KY",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        extraction_confidence=0.95,
        per_field_confidence={
            "brand_name": 0.95,
            "class_type": 0.9,
            "alcohol_content": 0.9,
            "net_contents": 0.9,
            "name_and_address": 0.9,
            "government_warning_body": 0.8,
        },
    )
    data.update(overrides)
    return ExtractedLabelData(**data)


def _jpeg_bytes(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _textured_image(
    width: int = 400,
    height: int = 300,
    color: tuple[int, int, int] = (160, 150, 140),
    *,
    blur: float = 0,
    brightness: float = 1.0,
    glare: bool = False,
) -> Image.Image:
    """High-contrast synthetic label so Laplacian / exposure have signal."""
    img = Image.new("RGB", (width, height), color)
    draw = ImageDraw.Draw(img)
    for x in range(0, width, 10):
        draw.line((x, 0, x, height), fill=(20, 20, 20), width=2)
    for y in range(0, height, 12):
        draw.line((0, y, width, y), fill=(230, 230, 230), width=1)
    draw.rectangle([20, 20, width - 20, 80], outline=(0, 0, 0), width=3)
    draw.text((30, 35), "GOVERNMENT WARNING", fill=(0, 0, 0))
    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if glare:
        overlay = ImageDraw.Draw(img)
        overlay.rectangle([0, 0, width, int(height * 0.35)], fill=(255, 255, 255))
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(radius=blur))
    return img


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    daily_spend_guard.reset_for_test()
    yield
    limiter.reset()
    daily_spend_guard.reset_for_test()


@pytest.fixture
def client():
    return TestClient(main_module.app)


def _post_review(client: TestClient, image_bytes: bytes, name: str = "label.jpg"):
    return client.post(
        "/api/review",
        files=[("files", (name, image_bytes, "image/jpeg"))],
        data={"application": json.dumps(_application())},
    )


def test_warn_mode_blur_attaches_advisory_and_calls_model(client, monkeypatch):
    monkeypatch.setenv("QUALITY_PREGATE_MODE", "warn")
    payload = open(os.path.join(QUAL, "blur_severe.jpg"), "rb").read()
    src = Image.open(io.BytesIO(payload))
    mocked = AsyncMock(return_value=(_readable_extraction(), None))
    with patch("app.main._extract_fields_with_retry", mocked):
        res = _post_review(client, payload, "blur.jpg")
    assert res.status_code == 200
    mocked.assert_called()
    body = res.json()
    assert body["error_code"] != "PHOTO_QUALITY_LOW"
    quality = body.get("quality")
    assert quality is not None
    assert "blur_score" in quality
    assert "exposure" in quality
    assert "glare_fraction" in quality
    assert "resolution" in quality
    assert quality["resolution"]["width"] == src.size[0]
    assert quality["resolution"]["height"] == src.size[1]
    assert any("focus" in w.lower() or "blur" in w.lower() for w in quality["warnings"])


def test_warn_mode_dark_attaches_advisory(client, monkeypatch):
    monkeypatch.setenv("QUALITY_PREGATE_MODE", "warn")
    img = _textured_image(brightness=0.25)
    mocked = AsyncMock(return_value=(_readable_extraction(), None))
    with patch("app.main._extract_fields_with_retry", mocked):
        res = _post_review(client, _jpeg_bytes(img), "dark.jpg")
    assert res.status_code == 200
    mocked.assert_called()
    quality = res.json()["quality"]
    assert quality["exposure"] == "dark"
    assert any("dark" in w.lower() for w in quality["warnings"])


def test_warn_mode_glare_attaches_advisory(client, monkeypatch):
    monkeypatch.setenv("QUALITY_PREGATE_MODE", "warn")
    payload = open(os.path.join(QUAL, "glare.jpg"), "rb").read()
    mocked = AsyncMock(return_value=(_readable_extraction(), None))
    with patch("app.main._extract_fields_with_retry", mocked):
        res = _post_review(client, payload, "glare.jpg")
    assert res.status_code == 200
    mocked.assert_called()
    quality = res.json()["quality"]
    assert quality["glare_fraction"] >= 0.0
    assert any("glare" in w.lower() for w in quality["warnings"])


def test_warn_mode_tiny_resolution_short_circuits(client, monkeypatch):
    monkeypatch.setenv("QUALITY_PREGATE_MODE", "warn")
    tiny = Image.new("RGB", (20, 20), (120, 130, 140))
    mocked = AsyncMock(return_value=(_readable_extraction(), None))
    with patch("app.main._extract_fields_with_retry", mocked):
        res = _post_review(client, _jpeg_bytes(tiny), "tiny.jpg")
    assert res.status_code == 200
    mocked.assert_not_called()
    body = res.json()
    assert body["overall_status"] == "retake"
    assert body["error_code"] == "PHOTO_QUALITY_LOW"
    assert body["error"] == RETAKE_MESSAGE
    assert body["retake"]["cause"] == "low_resolution"
    assert body["retake"]["field"] == "label"
    assert body["retake"]["tip"]
    assert body["processing_time_ms"] < 1000


def test_extreme_black_short_circuits_without_model(client, monkeypatch):
    monkeypatch.setenv("QUALITY_PREGATE_MODE", "warn")
    black = Image.new("RGB", (240, 180), (0, 0, 0))
    mocked = AsyncMock(return_value=(_readable_extraction(), None))
    with patch("app.main._extract_fields_with_retry", mocked):
        res = _post_review(client, _jpeg_bytes(black), "black.jpg")
    assert res.status_code == 200
    mocked.assert_not_called()
    body = res.json()
    assert body["overall_status"] == "retake"
    assert body["error_code"] == "PHOTO_QUALITY_LOW"
    assert body["error"] == RETAKE_MESSAGE
    assert body["retake"]["cause"] == "dark"


def test_label_check_extreme_black_no_model(client, monkeypatch):
    monkeypatch.setenv("QUALITY_PREGATE_MODE", "warn")
    black = Image.new("RGB", (240, 180), (0, 0, 0))
    mocked = AsyncMock(return_value=(_readable_extraction(), None))
    with patch("app.main._extract_fields_with_retry", mocked):
        res = client.post(
            "/api/label-check/batch",
            files=[("files", ("black.jpg", _jpeg_bytes(black), "image/jpeg"))],
            data={"image_counts": "[1]", "confirmed_beverage_type": "distilled_spirits"},
        )
    assert res.status_code == 200
    mocked.assert_not_called()
    body = res.json()[0]
    assert body["overall_status"] == "retake"
    assert body["error_code"] == "PHOTO_QUALITY_LOW"
    assert body["retake"]["cause"] == "dark"


def test_stream_extreme_tiny_no_model(client, monkeypatch):
    monkeypatch.setenv("QUALITY_PREGATE_MODE", "warn")
    tiny = Image.new("RGB", (16, 16), (90, 90, 90))
    mocked = MagicMock(return_value=_readable_extraction())
    with patch("app.main.extract_label_fields", mocked):
        res = client.post(
            "/api/review/batch/stream",
            files=[("files", ("tiny.jpg", _jpeg_bytes(tiny), "image/jpeg"))],
            data={
                "image_counts": "[1]",
                "applications": json.dumps([_application()]),
            },
        )
    assert res.status_code == 200
    mocked.assert_not_called()
    lines = [json.loads(line) for line in res.text.strip().split("\n") if line.strip()]
    result = next(item for item in lines if "result" in item)["result"]
    assert result["overall_status"] == "retake"
    assert result["error_code"] == "PHOTO_QUALITY_LOW"
    assert result["retake"]["cause"] == "low_resolution"


def test_block_mode_severe_blur_short_circuits(client, monkeypatch):
    monkeypatch.setenv("QUALITY_PREGATE_MODE", "block")
    path = os.path.join(QUAL, "blur_severe.jpg")
    mocked = AsyncMock(return_value=(_readable_extraction(), None))
    with patch("app.main._extract_fields_with_retry", mocked):
        res = _post_review(client, open(path, "rb").read(), "blur_severe.jpg")
    assert res.status_code == 200
    mocked.assert_not_called()
    body = res.json()
    assert body["overall_status"] == "retake"
    assert body["error_code"] == "PHOTO_QUALITY_LOW"
    assert body["retake"]["cause"] == "blur"


def test_off_mode_does_not_block_black(client, monkeypatch):
    monkeypatch.setenv("QUALITY_PREGATE_MODE", "off")
    black = Image.new("RGB", (240, 180), (0, 0, 0))
    mocked = AsyncMock(return_value=(_readable_extraction(), None))
    with patch("app.main._extract_fields_with_retry", mocked):
        res = _post_review(client, _jpeg_bytes(black), "black.jpg")
    assert res.status_code == 200
    mocked.assert_called()
    assert res.json().get("quality") is None


def test_unreadable_field_uses_pregate_cause(client, monkeypatch):
    monkeypatch.setenv("QUALITY_PREGATE_MODE", "warn")
    img = _textured_image(brightness=0.25)
    extracted = _readable_extraction(
        name_and_address="",
        per_field_confidence={
            "brand_name": 0.95,
            "class_type": 0.9,
            "alcohol_content": 0.9,
            "net_contents": 0.9,
            "name_and_address": 0.05,
            "government_warning_body": 0.8,
        },
    )
    mocked = AsyncMock(return_value=(extracted, None))
    with patch("app.main._extract_fields_with_retry", mocked):
        res = _post_review(client, _jpeg_bytes(img), "dark.jpg")
    assert res.status_code == 200
    body = res.json()
    assert body["overall_status"] == "retake"
    assert body["error_code"] == "PHOTO_UNREADABLE_FIELD"
    assert body["retake"]["cause"] == "dark"
    assert body["field_status"]["name_and_address"] == "unreadable"


def test_exif_orientation_transposed_on_intake():
    stored = Image.new("RGB", (200, 100), (200, 30, 30))
    for x in range(24):
        for y in range(12):
            stored.putpixel((x, y), (0, 255, 0))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW to display
    buf = io.BytesIO()
    stored.save(buf, format="JPEG", quality=95, exif=exif)
    raw = buf.getvalue()

    opened = Image.open(io.BytesIO(raw))
    assert opened.size == (200, 100)
    expected = ImageOps.exif_transpose(opened)
    assert expected is not None
    assert expected.size == (100, 200)

    intake = open_image_for_intake(raw, "sideways.jpg")
    assert intake.size == expected.size
    # Top-left of the corrected image must match PIL's official transpose.
    assert intake.getpixel((0, 0)) == expected.convert("RGB").getpixel((0, 0))

    processed, mime = prepare_image(raw, "sideways.jpg")
    assert mime == "image/jpeg"
    out = Image.open(io.BytesIO(processed))
    # Long edge of the stored 80x40 frame becomes the short edge after rotate.
    assert out.size[0] <= out.size[1]


def test_heic_decode_roundtrip_or_skip():
    pillow_heif = pytest.importorskip("pillow_heif")
    try:
        pillow_heif.register_heif_opener()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"libheif unavailable: {exc}")

    src = _textured_image(160, 120)
    buf = io.BytesIO()
    try:
        src.save(buf, format="HEIF")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"HEIC encode unavailable: {exc}")
    data = buf.getvalue()
    assert data[4:8] == b"ftyp"
    assert main_module._reject_if_not_image(data, "phone.heic") is None

    opened = open_image_for_intake(data, "phone.heic")
    assert opened.size == (160, 120)
    processed, mime = prepare_image(data, "phone.heic")
    assert mime == "image/jpeg"
    Image.open(io.BytesIO(processed)).verify()


def test_type_size_uses_original_full_resolution(client, monkeypatch):
    monkeypatch.setenv("QUALITY_PREGATE_MODE", "warn")
    original = _textured_image(3200, 2400)
    captured: dict = {}

    def _capture(image_bytes, filename="label.jpg", **kwargs):
        img = Image.open(io.BytesIO(image_bytes))
        captured["size"] = img.size
        captured["filename"] = filename
        return TypeSizeMeasurement(
            method="uncalibrated_estimate",
            required_min_height_mm=2.0,
            max_characters_per_inch=25,
            state="warning",
            verification_note="Uncalibrated photo without physical scale marker.",
        )

    mocked = AsyncMock(return_value=(_readable_extraction(), None))
    with (
        patch("app.main._extract_fields_with_retry", mocked),
        patch("app.main.evaluate_type_size_from_image_bytes", side_effect=_capture),
    ):
        res = _post_review(client, _jpeg_bytes(original), "big.jpg")
    assert res.status_code == 200
    mocked.assert_called()
    assert "size" in captured
    assert max(captured["size"]) == 3200
    assert min(captured["size"]) == 2400
    # Model payload is the downscaled copy; type-size must not use that.
    processed_args = mocked.call_args[0][0]
    processed_bytes = processed_args[0][0]
    processed_size = Image.open(io.BytesIO(processed_bytes)).size
    from app.claude_client import MAX_IMAGE_DIMENSION

    assert max(processed_size) <= MAX_IMAGE_DIMENSION
    assert MAX_IMAGE_DIMENSION == 1568
    assert max(captured["size"]) > max(processed_size)


@pytest.mark.parametrize(
    "endpoint,extra",
    [
        ("/api/review/batch", {"applications": json.dumps([_application()])}),
        ("/api/review/batch/stream", {"applications": json.dumps([_application()])}),
        ("/api/label-check/batch", {"confirmed_beverage_type": "distilled_spirits"}),
    ],
)
def test_malformed_image_counts_is_scrubbed(client, endpoint, extra):
    img = _textured_image(120, 120)
    res = client.post(
        endpoint,
        files=[("files", ("label.jpg", _jpeg_bytes(img), "image/jpeg"))],
        data={"image_counts": "not-json", **extra},
    )
    assert res.status_code == 422
    body = res.json()
    assert body["error_code"] == "INVALID_REQUEST"
    assert body["message"] == "image_counts must be a JSON list of integers."
    assert body["detail"] == "image_counts must be a JSON list of integers."
    dumped = json.dumps(body)
    assert "Expecting value" not in dumped
    assert "column 1" not in dumped
    assert body.get("request_id")
    assert res.headers.get("x-request-id") == body["request_id"]


def test_assess_image_quality_is_fast_on_12mp():
    """Sanity bound: a 12 MP working copy must stay well under a second."""
    import time

    img = _textured_image(4000, 3000)
    # Warm the importer / fonts so the timed call is representative.
    assess_image_quality(img)
    started = time.perf_counter()
    result = assess_image_quality(img)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert result.width == 4000
    assert result.height == 3000
    assert elapsed_ms < 500


def test_warn_mode_white_paper_label_is_not_blocked(client, monkeypatch):
    """Sparse black-on-white print must not be treated as extreme glare."""
    monkeypatch.setenv("QUALITY_PREGATE_MODE", "warn")
    img = Image.new("RGB", (800, 600), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((40, 40), "GOVERNMENT WARNING", fill=(0, 0, 0))
    draw.text((40, 80), "According to the Surgeon General", fill=(20, 20, 20))
    draw.rectangle([36, 36, 420, 120], outline=(0, 0, 0), width=2)
    assessment = assess_image_quality(img)
    assert assessment.should_block is False
    assert assessment.block_cause is None
    mocked = AsyncMock(return_value=(_readable_extraction(), None))
    with patch("app.main._extract_fields_with_retry", mocked):
        res = _post_review(client, _jpeg_bytes(img), "white-label.jpg")
    assert res.status_code == 200
    mocked.assert_called()
    body = res.json()
    assert body.get("error_code") != "PHOTO_QUALITY_LOW"
    assert body["overall_status"] != "retake" or body.get("error_code") != "PHOTO_QUALITY_LOW"


def test_solid_white_reports_blank_not_glare_or_low_resolution(client, monkeypatch):
    """Solid white frames are blank photos, not glare or low_resolution."""
    monkeypatch.setenv("QUALITY_PREGATE_MODE", "warn")
    white = Image.new("RGB", (400, 300), (255, 255, 255))
    assessment = assess_image_quality(white)
    assert assessment.exposure == "bright"
    assert assessment.glare_fraction >= 0.9
    assert assessment.should_block is True
    assert assessment.block_cause == "blank"
    assert assessment.retake_cause == "blank"

    mocked = AsyncMock(return_value=(_readable_extraction(), None))
    with patch("app.main._extract_fields_with_retry", mocked):
        res = _post_review(client, _jpeg_bytes(white), "white.jpg")
    assert res.status_code == 200
    mocked.assert_not_called()
    body = res.json()
    assert body["overall_status"] == "retake"
    assert body["error_code"] == "PHOTO_QUALITY_LOW"
    assert body["retake"]["cause"] == "blank"
    assert "glare" not in (body.get("error") or "").lower()
    assert "glare" not in (body.get("retake") or {}).get("tip", "").lower()
    assert "blank" in (body.get("error") or "").lower()
    quality = body.get("quality") or {}
    assert quality.get("exposure") == "bright"
    assert float(quality.get("glare_fraction") or 0) >= 0.9
    assert daily_spend_guard.get_count() == 0


def test_good_photo_is_not_blocked(client, monkeypatch):
    monkeypatch.setenv("QUALITY_PREGATE_MODE", "warn")
    path = os.path.join(QUAL, "sharp_welllit.jpg")
    mocked = AsyncMock(return_value=(_readable_extraction(), None))
    with patch("app.main._extract_fields_with_retry", mocked):
        res = _post_review(client, open(path, "rb").read(), "sharp.jpg")
    assert res.status_code == 200
    mocked.assert_called()
    body = res.json()
    assert body["overall_status"] != "retake" or body.get("error_code") != "PHOTO_QUALITY_LOW"
    assert body.get("error_code") in (None, "PHOTO_UNREADABLE_FIELD")
