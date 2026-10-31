"""Layer A: deterministic image-quality tests (no ANTHROPIC_API_KEY needed).

Exercises the blur/enhancement/prepare pipeline in app.claude_client and the
empirically-derived thresholds documented in
tests/fixtures/labels/quality/README.md.  The numbers below were measured
from the committed fixture corpus (see that README) and are asserted as an
ordering so the pipeline can't silently regress.
"""
import io
import os
import glob

import pytest
from PIL import Image

from app.claude_client import (
    ImageQualityError,
    _MIN_PIXEL_AREA,
    _enhance_for_ocr,
    _laplacian_variance,
    prepare_image,
)

HERE = os.path.dirname(__file__)
QUAL = os.path.join(HERE, "fixtures", "labels", "quality")
COMPLIANT = os.path.join(HERE, "fixtures", "labels", "compliant")


def _img(path):
    return Image.open(path).convert("RGB")


def _jpeg_bytes(img, quality=90):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


# Empirically-derived focus (true OpenCV Laplacian variance), measured from
# the committed fixtures: sharp ~2300, mild blur ~12, severe blur ~1.
BLUR_SHARP_MIN = 1000.0      # anything above this is comfortably in focus
BLUR_RETAKE_FLOOR = 8.0      # below this -> request a sharper photo (matches app FOCUS_RETAKE_FLOOR)


def test_laplacian_variance_orders_sharp_above_blurred():
    sharp = _laplacian_variance(_img(os.path.join(QUAL, "sharp_welllit.jpg")))
    mild = _laplacian_variance(_img(os.path.join(QUAL, "blur_mild.jpg")))
    severe = _laplacian_variance(_img(os.path.join(QUAL, "blur_severe.jpg")))
    assert sharp > mild > severe
    assert sharp > BLUR_SHARP_MIN
    assert severe < BLUR_RETAKE_FLOOR


def test_sharp_fixture_is_above_retake_floor():
    for name in ("sharp_welllit.jpg", "bright.jpg", "glare.jpg", "curved.jpg"):
        v = _laplacian_variance(_img(os.path.join(QUAL, name)))
        assert v > BLUR_RETAKE_FLOOR, f"{name} unexpectedly below focus floor: {v}"


def test_severe_blur_is_below_retake_floor():
    v = _laplacian_variance(_img(os.path.join(QUAL, "blur_severe.jpg")))
    assert v < BLUR_RETAKE_FLOOR


def test_enhance_for_ocr_returns_same_size_rgb_image():
    src = _img(os.path.join(QUAL, "blur_mild.jpg"))
    out = _enhance_for_ocr(src)
    assert isinstance(out, Image.Image)
    assert out.size == src.size


def test_enhance_does_not_destroy_focus_on_blurred_input():
    src = _img(os.path.join(QUAL, "blur_mild.jpg"))
    before = _laplacian_variance(src)
    after = _laplacian_variance(_enhance_for_ocr(src))
    # Sharpening must not make a mildly-blurred image measurably worse.
    assert after >= before * 0.9


def test_prepare_image_accepts_good_label_and_returns_jpeg():
    data = _jpeg_bytes(_img(os.path.join(COMPLIANT, "all_elements_present.jpg")))
    out_bytes, mime = prepare_image(data, "all_elements_present.jpg")
    assert isinstance(out_bytes, (bytes, bytearray))
    assert mime == "image/jpeg"
    # Output must be a decodable image.
    Image.open(io.BytesIO(out_bytes)).verify()


def test_prepare_image_rejects_tiny_image():
    tiny = Image.new("RGB", (20, 20), "white")
    with pytest.raises(ImageQualityError):
        prepare_image(_jpeg_bytes(tiny), "tiny.jpg")


def test_prepare_image_downscales_oversized_image():
    big = Image.new("RGB", (6000, 6000), "white")
    out_bytes, _ = prepare_image(_jpeg_bytes(big), "big.jpg")
    w, h = Image.open(io.BytesIO(out_bytes)).size
    from app.claude_client import MAX_IMAGE_DIMENSION

    assert max(w, h) <= MAX_IMAGE_DIMENSION
    assert MAX_IMAGE_DIMENSION == 1568
    assert max(w, h) < 6000


def test_min_pixel_area_constant_is_sane():
    assert _MIN_PIXEL_AREA == 100 * 100
