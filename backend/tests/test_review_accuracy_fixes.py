"""Unit tests for the 2026-10-27 review-accuracy fixes.

Covers the card detector, missing-vs-unreadable fields, review-mode rules
(SOF, proof-only ABV, sulfites, bold header), exact Government Warning text
with an OCR second look, blank/non-label messages, concurrent streaming,
named 422 validation, prompt-injection hardening, a true Laplacian, and
HEIC ``ftyp`` recognition. No live vision calls.
"""

from __future__ import annotations

import inspect
import io
import json
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFilter

import app.claude_client as claude_client
import app.cv_typesize as cv_typesize
import app.main as main_module
from app.claude_client import SYSTEM_PROMPT, _is_heif_bytes, _laplacian_variance
from app.compliance import (
    CANONICAL_WARNING_BODY,
    CANONICAL_WARNING_HEADER,
    _alcohol_is_proof_only,
    _check_government_warning,
    analyze_readability,
    check_label_requirements,
    diff_warning_words,
    run_compliance_checks,
    warning_looks_like_ocr_noise,
    warning_needs_second_look,
)
from app.errors import BLANK_IMAGE_MESSAGE, BLANK_IMAGE_TIP, NOT_A_LABEL_MESSAGE
from app.models import ApplicationData, CalibrationMethod, ExtractedLabelData


def _application(**overrides) -> ApplicationData:
    base = dict(
        beverage_type="distilled_spirits",
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
    )
    base.update(overrides)
    return ApplicationData(**base)


def _extracted(**overrides) -> ExtractedLabelData:
    base = dict(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        name_and_address="Bottled by Old Tom Distillery, Louisville, KY",
        government_warning_present=True,
        government_warning_header=CANONICAL_WARNING_HEADER,
        government_warning_body=CANONICAL_WARNING_BODY,
        extraction_confidence=0.95,
        per_field_confidence={
            "brand_name": 0.95,
            "class_type": 0.95,
            "alcohol_content": 0.95,
            "net_contents": 0.95,
            "name_and_address": 0.95,
            "government_warning_body": 0.9,
        },
    )
    base.update(overrides)
    return ExtractedLabelData(**base)


def _png_bytes(width: int = 200, height: int = 160, color=(80, 120, 160)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _label_sized_rectangle_bgr(canvas=(1200, 900), frac=0.45) -> np.ndarray:
    """Draw a high-contrast rectangle with ID-1 aspect that fills *frac* of the frame."""
    w, h = canvas
    img = Image.new("RGB", (w, h), (230, 230, 230))
    draw = ImageDraw.Draw(img)
    aspect = cv_typesize.ID1_ASPECT_RATIO
    area = frac * w * h
    box_h = int((area / aspect) ** 0.5)
    box_w = int(box_h * aspect)
    x0 = (w - box_w) // 2
    y0 = (h - box_h) // 2
    draw.rectangle([x0, y0, x0 + box_w, y0 + box_h], fill=(20, 20, 20), outline=(0, 0, 0), width=4)
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def _small_id1_card_bgr(canvas=(1800, 1400)) -> np.ndarray:
    """True-scale ID-1 card (~18% of an 1800×1400 frame) beside empty space."""
    w, h = canvas
    img = Image.new("RGB", (w, h), (240, 240, 240))
    draw = ImageDraw.Draw(img)
    px_per_mm = 10.0
    card_w = int(cv_typesize.ID1_WIDTH_MM * px_per_mm)
    card_h = int(cv_typesize.ID1_HEIGHT_MM * px_per_mm)
    draw.rectangle([60, 80, 60 + card_w, 80 + card_h], fill=(30, 30, 30), outline=(5, 5, 5), width=3)
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


# ---------------------------------------------------------------------------
# Type-size / ID-1 detector
# ---------------------------------------------------------------------------


class TestId1CardDetector:
    def test_label_sized_rectangle_is_not_a_card(self):
        image = _label_sized_rectangle_bgr(frac=0.45)
        assert cv_typesize.detect_id1_marker_contour(image) is None

    def test_small_id1_card_is_still_found(self):
        image = _small_id1_card_bgr()
        found = cv_typesize.detect_id1_marker_contour(image)
        assert found is not None
        _quad, px_per_mm, _skew = found
        assert 8.0 < px_per_mm < 12.0

    def test_quad_containing_warning_is_rejected(self):
        image = _small_id1_card_bgr()
        # Card sits at top-left; a bbox centered on that card must be rejected.
        gw_bbox = (60 / 1800, 80 / 1400, 856 / 1800, 540 / 1400)
        assert cv_typesize.detect_id1_marker_contour(image, gw_bbox=gw_bbox) is None

    def test_detector_constants_match_prototype(self):
        assert cv_typesize.ID1_ASPECT_TOLERANCE == pytest.approx(0.06)
        assert cv_typesize.ID1_MAX_AREA_FRACTION == pytest.approx(0.30)
        assert cv_typesize.CAP_HEIGHT_PERCENTILE == pytest.approx(80.0)

    def test_guarded_geometry_skips_clahe(self):
        source = inspect.getsource(cv_typesize.apply_guarded_geometry)
        assert "createCLAHE" not in source
        assert "intentionally omitted" in source
        assert "_upscale_small" in source
        assert "_flatten_label_quad" in source
        assert "_dewarp_cylinder" in source

    def test_evaluate_type_size_detects_markers_before_warp(self):
        source = inspect.getsource(cv_typesize.evaluate_type_size_from_image_bytes)
        assert "detect_id1_marker_contour" in source
        assert "gw_bbox=gw_bbox" in source
        assert "apply_guarded_geometry" not in source
        assert "_flatten_label_quad" not in source

    def test_flatten_skips_non_quad(self):
        image = np.full((400, 400, 3), 240, dtype=np.uint8)
        cv2.circle(image, (200, 200), 90, (20, 20, 20), -1)
        out = cv_typesize._flatten_label_quad(image)
        assert out.shape == image.shape
        assert np.array_equal(out, image)

    def test_evaluate_type_size_keeps_card_beside_label(self):
        canvas = Image.new("RGB", (1800, 1400), (40, 40, 40))
        draw = ImageDraw.Draw(canvas)
        px_per_mm = 10.0
        card_w = int(cv_typesize.ID1_WIDTH_MM * px_per_mm)
        card_h = int(cv_typesize.ID1_HEIGHT_MM * px_per_mm)
        draw.rectangle([40, 80, 40 + card_w, 80 + card_h], fill=(230, 230, 230), outline=(10, 10, 10), width=3)
        draw.rectangle([980, 40, 1760, 1340], fill=(250, 248, 240))
        image = cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)
        flattened = cv_typesize._flatten_label_quad(image)
        assert cv_typesize.detect_id1_marker_contour(image) is not None
        assert cv_typesize.detect_id1_marker_contour(flattened) is None
        ok, encoded = cv2.imencode(".png", image)
        assert ok
        result = cv_typesize.evaluate_type_size_from_image_bytes(
            encoded.tobytes(),
            filename="card-beside.png",
            net_contents="750 mL",
            gw_bbox=(0.55, 0.70, 0.30, 0.15),
        )
        assert result.method == CalibrationMethod.SCALE_MARKER_CARD_ID1
        assert result.pixels_per_mm is not None


class TestCapHeightPercentile:
    def test_percentile_tracks_taller_cluster(self):
        heights = [10.0] * 6 + [20.0] * 4  # mixed x-height vs caps
        median = float(np.median(heights))
        p80 = float(np.percentile(heights, cv_typesize.CAP_HEIGHT_PERCENTILE))
        assert p80 > median
        assert p80 == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Missing field ≠ retake
# ---------------------------------------------------------------------------


class TestAbsentVersusUnreadable:
    def test_empty_zero_on_readable_photo_is_absent(self):
        ext = _extracted(
            name_and_address="",
            per_field_confidence={"name_and_address": 0.0, "brand_name": 0.95},
            extraction_confidence=0.95,
        )
        analysis = analyze_readability(ext)
        assert analysis.field_status["name_and_address"] == "absent"
        assert analysis.has_required_unreadable is False

    def test_low_nonzero_score_is_unreadable(self):
        ext = _extracted(
            name_and_address="",
            per_field_confidence={"name_and_address": 0.12, "brand_name": 0.95},
            extraction_confidence=0.95,
        )
        analysis = analyze_readability(ext)
        assert analysis.field_status["name_and_address"] == "unreadable"
        assert analysis.has_required_unreadable is True

    def test_absent_field_uses_application_type_not_guess(self):
        ext = _extracted(
            name_and_address="",
            beverage_type_guess=None,
            extraction_confidence=0.48,
            per_field_confidence={"name_and_address": 0.0, "brand_name": 0.95},
        )
        unknown = analyze_readability(ext)
        assert unknown.field_status["name_and_address"] == "unreadable"
        beer = analyze_readability(ext, beverage_type="beer")
        assert beer.field_status["name_and_address"] == "absent"
        results = run_compliance_checks(
            _application(
                beverage_type="beer",
                alcohol_content="5% Alc./Vol.",
                name_and_address="Bottled by Old Tom Distillery, Louisville, KY",
            ),
            ext,
        )
        addr = next(item for item in results if item.field == "name_and_address")
        assert addr.status == "fail"
        assert "not found" in addr.message.lower()

    def test_review_reports_not_found_not_retake(self):
        ext = _extracted(
            alcohol_content="",
            per_field_confidence={"alcohol_content": 0.0, "brand_name": 0.95},
            extraction_confidence=0.97,
        )
        results = run_compliance_checks(_application(), ext)
        abv = next(item for item in results if item.field == "alcohol_content")
        assert abv.status == "fail"
        assert "not found" in abv.message.lower()


# ---------------------------------------------------------------------------
# Review-mode rules
# ---------------------------------------------------------------------------


class TestReviewModeRules:
    def test_nonstandard_fill_fails_in_review(self):
        results = run_compliance_checks(
            _application(net_contents="650 mL"),
            _extracted(net_contents="650 mL"),
        )
        net = next(item for item in results if item.field == "net_contents")
        assert net.status == "fail"
        assert "standard" in net.message.lower() or "fill" in net.message.lower()

    def test_proof_only_spirits_fails_in_review(self):
        assert _alcohol_is_proof_only("90 Proof") is True
        assert _alcohol_is_proof_only("45% Alc./Vol. (90 Proof)") is False
        results = run_compliance_checks(
            _application(alcohol_content="90 Proof"),
            _extracted(alcohol_content="90 Proof"),
        )
        abv = next(item for item in results if item.field == "alcohol_content")
        assert abv.status == "fail"
        assert "proof" in abv.message.lower()
        assert "percent" in abv.message.lower()

    def test_proof_only_beer_fails_in_review(self):
        results = run_compliance_checks(
            _application(beverage_type="beer", alcohol_content="10 Proof"),
            _extracted(alcohol_content="10 Proof", beverage_type_guess="beer"),
        )
        abv = next(item for item in results if item.field == "alcohol_content")
        assert abv.status == "fail"

    def test_wine_missing_sulfites_is_warning_in_review(self):
        results = run_compliance_checks(
            _application(beverage_type="wine", alcohol_content="13.5% Alc./Vol."),
            _extracted(
                beverage_type_guess="wine",
                alcohol_content="13.5% Alc./Vol.",
                sulfite_declaration=None,
            ),
        )
        sulfite = next(item for item in results if item.field == "sulfite_declaration")
        assert sulfite.status == "warning"
        assert "sulfite" in sulfite.message.lower()

    def test_allergen_absent_is_informational_pass(self):
        results = check_label_requirements(
            _extracted(allergen_statements=None, beverage_type_guess="wine"),
            beverage_type="wine",
        )
        allergen = next(item for item in results if item.field == "allergen_statements")
        assert allergen.status == "pass"
        assert "informational" in allergen.message.lower()


# ---------------------------------------------------------------------------
# Government Warning exactness + second look
# ---------------------------------------------------------------------------


class TestWarningExactness:
    def test_altered_wording_fails(self):
        body = CANONICAL_WARNING_BODY.replace("impairs", "may impair")
        result = _check_government_warning(
            _extracted(government_warning_body=body)
        )
        assert result.status == "fail"
        assert result.warning_text_diff is not None
        assert result.warning_text_diff.exact_match is False

    def test_close_ocr_substitution_is_noise(self):
        observed = CANONICAL_WARNING_HEADER + " " + CANONICAL_WARNING_BODY.replace(
            "impairs", "impairz"
        )
        diff = diff_warning_words(
            CANONICAL_WARNING_HEADER + " " + CANONICAL_WARNING_BODY, observed
        )
        assert warning_looks_like_ocr_noise(diff) is True
        assert warning_needs_second_look(diff, observed) is True

    def test_real_word_change_is_not_noise(self):
        observed = CANONICAL_WARNING_HEADER + " " + CANONICAL_WARNING_BODY.replace(
            "impairs", "may impair"
        )
        diff = diff_warning_words(
            CANONICAL_WARNING_HEADER + " " + CANONICAL_WARNING_BODY, observed
        )
        assert warning_looks_like_ocr_noise(diff) is False
        assert warning_needs_second_look(diff, observed) is False

    def test_second_look_can_confirm_exact_text(self):
        body = CANONICAL_WARNING_BODY.replace("impairs", "impairz")
        confirmed = CANONICAL_WARNING_HEADER + " " + CANONICAL_WARNING_BODY
        result = _check_government_warning(
            _extracted(government_warning_body=body),
            second_look=lambda _observed: confirmed,
        )
        assert result.status == "pass"

    def test_unconfirmed_second_look_asks_for_retake(self):
        body = CANONICAL_WARNING_BODY.replace("impairs", "impairz")
        result = _check_government_warning(
            _extracted(government_warning_body=body),
            second_look=lambda _observed: None,
        )
        assert result.status == "warning"
        assert "retake" in result.message.lower()


# ---------------------------------------------------------------------------
# Blank / not-a-label messages
# ---------------------------------------------------------------------------


class TestSpecificImageMessages:
    def test_not_a_label_message_is_specific(self):
        assert "does not appear to be an alcohol beverage" in NOT_A_LABEL_MESSAGE
        assert "Unexpected error" not in NOT_A_LABEL_MESSAGE

    def test_blank_message_is_specific(self):
        assert "blank" in BLANK_IMAGE_MESSAGE.lower()
        assert "Unexpected error" not in BLANK_IMAGE_MESSAGE

    def test_value_error_keeps_not_a_label_text(self):
        err = main_module._client_error_from_exception(ValueError(NOT_A_LABEL_MESSAGE))
        assert "alcohol beverage" in err.message.lower()
        assert "unexpected error" not in err.message.lower()

    def test_blank_white_photo_uses_blank_message(self, monkeypatch):
        monkeypatch.setenv("QUALITY_PREGATE_MODE", "warn")
        white = Image.new("RGB", (400, 300), (255, 255, 255))
        buf = io.BytesIO()
        white.save(buf, format="PNG")
        result = main_module._intake_one_image(buf.getvalue(), "blank.png")
        assert isinstance(result, main_module.ClientError)
        assert result.message == BLANK_IMAGE_MESSAGE
        assert result.retake is not None
        assert result.retake.cause == "blank"
        assert result.retake.tip == BLANK_IMAGE_TIP
        assert "glare" not in result.retake.tip.lower()
        assert "glare" not in result.message.lower()
        assert "label" in result.retake.tip.lower()

    def test_blank_image_tip_does_not_blame_glare(self):
        assert "glare" not in BLANK_IMAGE_TIP.lower()
        assert "label" in BLANK_IMAGE_TIP.lower()
        assert "frame" in BLANK_IMAGE_TIP.lower() or "printed" in BLANK_IMAGE_TIP.lower()


# ---------------------------------------------------------------------------
# Streaming concurrency + named 422
# ---------------------------------------------------------------------------


class TestStreamAndValidation:
    def test_stream_uses_create_task_and_as_completed(self):
        source = inspect.getsource(main_module.review_labels_batch_stream)
        assert "create_task" in source
        assert "as_completed" in source
        assert "_stream_one" in source

    def test_stream_reviews_labels_concurrently(self):
        from app.limiter import daily_spend_guard, limiter

        limiter.reset()
        daily_spend_guard.reset_for_test()
        client = TestClient(main_module.app)
        starts: list[float] = []
        import time

        def _slow_extract(*_args, **_kwargs):
            starts.append(time.monotonic())
            time.sleep(0.25)
            return _extracted()

        with patch("app.main.extract_label_fields", side_effect=_slow_extract):
            t0 = time.monotonic()
            res = client.post(
                "/api/review/batch/stream",
                files=[
                    ("files", ("a.png", _png_bytes(), "image/png")),
                    ("files", ("b.png", _png_bytes(), "image/png")),
                ],
                data={
                    "image_counts": "[1,1]",
                    "applications": json.dumps(
                        [
                            {
                                "beverage_type": "distilled_spirits",
                                "brand_name": "OLD TOM DISTILLERY",
                                "class_type": "Whiskey",
                                "alcohol_content": "45% Alc./Vol.",
                                "net_contents": "750 mL",
                            },
                            {
                                "beverage_type": "distilled_spirits",
                                "brand_name": "OLD TOM DISTILLERY",
                                "class_type": "Whiskey",
                                "alcohol_content": "45% Alc./Vol.",
                                "net_contents": "750 mL",
                            },
                        ]
                    ),
                },
            )
            elapsed = time.monotonic() - t0
        assert res.status_code == 200
        assert len(starts) == 2
        assert abs(starts[0] - starts[1]) < 0.20
        assert elapsed < 0.45

    def test_invalid_application_names_the_field(self):
        client = TestClient(main_module.app)
        payload = {
            "beverage_type": "distilled_spirits",
            "class_type": "Whiskey",
            "alcohol_content": "45% Alc./Vol.",
            "net_contents": "750 mL",
        }
        res = client.post(
            "/api/review",
            files=[("files", ("front.png", _png_bytes(), "image/png"))],
            data={"application": json.dumps(payload)},
        )
        assert res.status_code == 422
        body = res.json()
        assert body["error_code"] == "INVALID_REQUEST"
        assert "brand_name" in body["message"]
        assert body.get("field") == "brand_name" or "brand_name" in body["detail"]

    def test_invalid_image_counts_is_422(self):
        client = TestClient(main_module.app)
        res = client.post(
            "/api/review/batch",
            files=[("files", ("front.png", _png_bytes(), "image/png"))],
            data={
                "image_counts": "not-json",
                "applications": json.dumps(
                    [
                        {
                            "beverage_type": "distilled_spirits",
                            "brand_name": "OLD TOM DISTILLERY",
                            "class_type": "Whiskey",
                            "alcohol_content": "45% Alc./Vol.",
                            "net_contents": "750 mL",
                        }
                    ]
                ),
            },
        )
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# Prompt injection + Laplacian + HEIC
# ---------------------------------------------------------------------------


class TestPromptAndImaging:
    def test_system_prompt_marks_label_text_untrusted(self):
        assert "untrusted" in SYSTEM_PROMPT.lower()
        assert "ignore previous instructions" in SYSTEM_PROMPT.lower()
        assert "0.0" in SYSTEM_PROMPT
        assert "0.10-0.30" in SYSTEM_PROMPT

    def test_user_prompt_also_marks_label_text_untrusted(self):
        source = inspect.getsource(claude_client.extract_label_fields_with_usage)
        assert "untrusted data" in source

    def test_laplacian_uses_opencv(self):
        source = inspect.getsource(_laplacian_variance)
        assert "cv2.Laplacian" in source
        assert "return float(cv2.Laplacian" in source

    def test_laplacian_scores_sharp_above_blur(self):
        sharp = Image.new("RGB", (240, 180), (40, 40, 40))
        draw = ImageDraw.Draw(sharp)
        for x in range(0, 240, 8):
            draw.line([(x, 0), (x, 180)], fill=(240, 240, 240), width=2)
        blurred = sharp.filter(ImageFilter.GaussianBlur(radius=6))
        assert _laplacian_variance(sharp) > _laplacian_variance(blurred) * 3

    def test_heic_ftyp_is_recognized(self):
        payload = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 20
        assert _is_heif_bytes(payload) is True
        assert _is_heif_bytes(b"not-an-image") is False
        assert _is_heif_bytes(b"\x00\x00\x00\x18xxxxheic") is False
