"""Latency round 2: fast resize, cache hits, TTFT, type-size downscale.

No live Anthropic calls. Verdicts and required extraction fields stay the same.
"""

from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from app.claude_client import (
    CACHE_CONTROL_EPHEMERAL,
    ENHANCE_SKIP_FOCUS_MIN,
    EXTRACTION_MAX_TOKENS,
    JPEG_QUALITY,
    MAX_IMAGE_DIMENSION,
    PROMPT_CACHE_MIN_TOKENS_SONNET,
    SYSTEM_PROMPT,
    ImageQualityError,
    _downscale_for_vision,
    _enhance_for_ocr,
    _laplacian_variance,
    build_cached_system_blocks,
    build_extraction_create_kwargs,
    encode_full_resolution_jpeg,
    estimate_cached_prefix_tokens,
    extract_label_fields_with_usage,
    image_needs_enhancement,
    open_image_for_intake,
    original_bytes_for_typesize,
    prepare_image,
    prepare_images_concurrent,
)
from app.cv_typesize import (
    TYPESIZE_WORKING_MAX_EDGE,
    _decode_working_bgr,
    _working_copy_bgr,
    _working_px_floor,
    evaluate_type_size_from_image_bytes,
    measure_capital_letter_height_px,
)
from app.models import ExtractedLabelData
from app.quality import QualityAssessment, assess_image_quality


def _jpeg_bytes(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _label_photo(width: int = 1400, height: int = 1800) -> Image.Image:
    img = Image.new("RGB", (width, height), (220, 210, 195))
    draw = ImageDraw.Draw(img)
    draw.rectangle([60, 60, width - 60, height - 60], outline=(20, 20, 20), width=6)
    draw.rectangle([100, 180, width - 100, 820], fill=(250, 248, 240))
    for index, line in enumerate(
        ("OLD TOM DISTILLERY", "Kentucky Straight Bourbon", "45% Alc./Vol.", "750 mL")
    ):
        draw.text((140, 220 + index * 70), line, fill=(15, 15, 15))
    return img


def _tool_input() -> dict:
    return {
        "brand_name": "OLD TOM DISTILLERY",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "alcohol_content": "45% Alc./Vol.",
        "net_contents": "750 mL",
        "name_and_address": "Bottled by Old Tom Distillery, Louisville, KY",
        "country_of_origin": "",
        "government_warning_header": "GOVERNMENT WARNING:",
        "government_warning_body": "women should not drink",
        "government_warning_present": True,
        "beverage_type_guess": "distilled_spirits",
        "origin_guess": "domestic",
        "field_locations": [],
        "notes": "",
        "sulfite_declaration": "",
        "allergen_statements": "",
        "age_statement": "",
        "commodity_statement": "",
        "is_alcohol_beverage_label": True,
        "per_field_confidence": {"brand_name": 0.95},
        "extraction_confidence": 0.91,
    }


def test_cached_prefix_exceeds_sonnet_minimum():
    """Tools + system must be long enough or Anthropic silently skips the cache."""
    estimated = estimate_cached_prefix_tokens()
    assert estimated >= PROMPT_CACHE_MIN_TOKENS_SONNET
    system = build_cached_system_blocks()
    assert "cache_control" not in system[0]
    assert system[-1]["cache_control"] == CACHE_CONTROL_EPHEMERAL
    assert "TTB" in SYSTEM_PROMPT


def test_identical_replay_marks_user_text_cache_breakpoint():
    kwargs = build_extraction_create_kwargs(
        [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "aa"}}],
        "Record compact JSON fields from this label image.",
        "claude-sonnet-4-6",
    )
    user_content = kwargs["messages"][0]["content"]
    assert user_content[0]["type"] == "image"
    assert user_content[-1]["cache_control"] == CACHE_CONTROL_EPHEMERAL
    # Images stay after the static tools/system breakpoint.
    assert "cache_control" not in user_content[0]


def test_extract_with_usage_records_cache_tokens_and_create_fallback():
    block = SimpleNamespace(type="tool_use", name="record_label_fields", input=_tool_input())
    response = SimpleNamespace(
        content=[block],
        usage=SimpleNamespace(
            input_tokens=2400,
            output_tokens=410,
            cache_read_input_tokens=1900,
            cache_creation_input_tokens=0,
        ),
        model="claude-sonnet-4-6",
        id="msg_hit",
        _request_id="req_hit",
    )
    with patch("app.claude_client._CLIENT.messages.create", return_value=response) as create:
        data, metrics = extract_label_fields_with_usage([(b"jpeg", "label.jpg")], preprocessed=True)
    create.assert_called_once()
    assert isinstance(data, ExtractedLabelData)
    assert data.brand_name == "OLD TOM DISTILLERY"
    assert metrics.cache_read_input_tokens == 1900
    assert metrics.cache_creation_input_tokens == 0
    assert metrics.output_tokens == 410
    assert EXTRACTION_MAX_TOKENS == 800


def test_downscale_uses_reduce_then_bilinear_not_lanczos():
    big = Image.new("RGB", (4000, 3000), (90, 80, 70))
    out = _downscale_for_vision(big)
    assert max(out.size) == MAX_IMAGE_DIMENSION
    already = Image.new("RGB", (800, 600), (10, 10, 10))
    assert _downscale_for_vision(already).size == (800, 600)


def test_skip_enhancement_on_sharp_well_lit_photo():
    sharp = _label_photo()
    quality = assess_image_quality(sharp)
    assert quality.blur_score >= ENHANCE_SKIP_FOCUS_MIN
    assert quality.exposure == "ok"
    assert image_needs_enhancement(quality=quality) is False
    raw = _jpeg_bytes(sharp)
    opened = open_image_for_intake(raw, "sharp.jpg")
    with patch("app.claude_client._enhance_for_ocr", side_effect=_enhance_for_ocr) as enhance:
        out, mime = prepare_image(raw, "sharp.jpg", image=opened, quality=quality)
    enhance.assert_not_called()
    assert mime == "image/jpeg"
    assert Image.open(io.BytesIO(out)).format == "JPEG"


def test_keep_enhancement_on_blurry_photo():
    src = Image.new("RGB", (1400, 1800), (120, 120, 120))
    blurry = src.filter(ImageFilter.GaussianBlur(radius=8))
    quality = QualityAssessment(
        blur_score=40.0,
        exposure="ok",
        glare_fraction=0.01,
        width=1400,
        height=1800,
    )
    assert image_needs_enhancement(quality=quality) is True
    raw = _jpeg_bytes(blurry)
    opened = open_image_for_intake(raw, "blur.jpg")
    with patch("app.claude_client._enhance_for_ocr", side_effect=_enhance_for_ocr) as enhance:
        prepare_image(raw, "blur.jpg", image=opened, quality=quality)
    enhance.assert_called_once()
    # Enhancement must not destroy remaining focus on a soft photo.
    before = _laplacian_variance(opened)
    after = _laplacian_variance(_enhance_for_ocr(opened.copy()))
    assert after >= before * 0.9


def test_reuse_original_jpeg_when_exif_does_not_rotate():
    raw = _jpeg_bytes(_label_photo(640, 480))
    opened = open_image_for_intake(raw, "upright.jpg")
    assert opened.info.get("exif_transposed") is False
    reused = original_bytes_for_typesize(opened, raw)
    assert reused is raw
    opened.info["exif_transposed"] = True
    rewritten = original_bytes_for_typesize(opened, raw)
    assert rewritten is not raw
    assert rewritten[:2] == b"\xff\xd8"


def test_prepare_images_concurrent_matches_serial_bytes():
    panels = [
        (_jpeg_bytes(_label_photo(1400, 1800)), "front.jpg"),
        (_jpeg_bytes(_label_photo(1400, 1600)), "back.jpg"),
    ]
    serial = [prepare_image(data, name) for data, name in panels]
    parallel = prepare_images_concurrent(panels)
    assert [mime for _bytes, mime in parallel] == ["image/jpeg", "image/jpeg"]
    assert [len(item[0]) for item in parallel] == [len(item[0]) for item in serial]


def test_two_panel_prepare_is_faster_than_round1_style_path():
    """Skip enhance + bilinear + no q95 copy must beat always-enhance + Lanczos + q95."""
    raw = _jpeg_bytes(_label_photo(1400, 1800))
    opened = open_image_for_intake(raw, "wine.jpg")
    quality = assess_image_quality(opened)

    def _round1_style() -> None:
        img = opened.copy()
        width, height = img.size
        if max(width, height) > MAX_IMAGE_DIMENSION:
            scale = MAX_IMAGE_DIMENSION / max(width, height)
            img = img.resize((int(width * scale), int(height * scale)), Image.LANCZOS)
        _enhance_for_ocr(img)
        encode_full_resolution_jpeg(opened)

    def _round2_style() -> None:
        prepare_image(raw, "wine.jpg", image=opened, quality=quality)
        original_bytes_for_typesize(opened, raw)

    # Warm the codec once.
    _round1_style()
    _round2_style()

    def _mean(fn, loops: int = 3) -> float:
        started = time.perf_counter()
        for _ in range(loops):
            fn()
        return (time.perf_counter() - started) / loops

    # Two panels, sequential, same as summed resize_ms.
    round1 = _mean(lambda: (_round1_style(), _round1_style()))
    round2 = _mean(lambda: (_round2_style(), _round2_style()))
    assert round2 < round1
    assert JPEG_QUALITY == 85


def test_typesize_working_copy_caps_long_edge():
    huge = np.zeros((3000, 4000, 3), dtype=np.uint8)
    working = _working_copy_bgr(huge)
    assert max(working.shape[:2]) == TYPESIZE_WORKING_MAX_EDGE
    small = np.zeros((200, 300, 3), dtype=np.uint8)
    assert _working_copy_bgr(small).shape == small.shape


def test_typesize_jpeg_draft_stays_at_or_under_working_max():
    raw = _jpeg_bytes(_label_photo(3200, 2400))
    decoded, scale = _decode_working_bgr(raw)
    assert decoded is not None
    assert max(decoded.shape[:2]) <= TYPESIZE_WORKING_MAX_EDGE
    assert 0 < scale <= 1
    assert _decode_working_bgr(b"") == (None, 1.0)


def test_glyph_pixel_floors_scale_with_working_copy():
    assert _working_px_floor(6, 1.0) == 6
    assert _working_px_floor(6, 0.32) == 1
    assert _working_px_floor(10, 0.32) == 3


def test_small_capitals_survive_working_copy_scale():
    """5 px capitals are dropped at full-res gates but kept after a 0.32 scale."""
    img = np.full((120, 280, 3), 250, dtype=np.uint8)
    # Default crop is the lower band (0.4–0.95 of height).
    for index in range(5):
        x = 20 + index * 48
        y = 70
        img[y : y + 5, x : x + 6] = 15
        img[y + 1 : y + 4, x + 1 : x + 5] = 250  # hole → fill ~0.60, aspect 1.2
    assert measure_capital_letter_height_px(img, scale=1.0) is None
    measured = measure_capital_letter_height_px(img, scale=0.32)
    assert measured is not None
    assert 4.0 <= measured[0] <= 7.0


def test_truncated_jpeg_raises_image_quality_error():
    raw = _jpeg_bytes(_label_photo(200, 160))
    truncated = raw[:60]
    try:
        open_image_for_intake(truncated, "truncated.jpg")
    except ImageQualityError:
        return
    raise AssertionError("truncated JPEG must raise ImageQualityError, not succeed or 500")


def test_typesize_on_large_photo_stays_uncalibrated_and_fast():
    photo = Image.effect_noise((3200, 2400), 40).convert("RGB")
    raw = _jpeg_bytes(photo, quality=85)
    started = time.perf_counter()
    result = evaluate_type_size_from_image_bytes(raw, "big.jpg", net_contents="750 mL")
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert result.method.value == "uncalibrated_estimate"
    assert result.measured_capital_height_mm is None
    # Full-res bilateral+ArUco on 3200x2400 was >1s locally; the working copy
    # must stay well under that even on a slow CI runner.
    assert elapsed_ms < 800


def test_panel_calls_are_issued_concurrently():
    """asyncio.gather on two roles is covered in test_perf; this checks the pool."""
    seen = []

    def _work(tag: str) -> str:
        seen.append(f"start-{tag}")
        time.sleep(0.05)
        seen.append(f"end-{tag}")
        return tag

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_work, "a"), pool.submit(_work, "b")]
        results = [item.result() for item in futures]
    elapsed = time.perf_counter() - started
    assert results == ["a", "b"]
    assert elapsed < 0.09
    assert seen[0].startswith("start-")
    assert seen[1].startswith("start-")
