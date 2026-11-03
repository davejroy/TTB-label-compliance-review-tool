"""Photo quality pre-gate: warn-first intake diagnostics.

``diagnose_image_quality`` (re-exported from this module's call into
``claude_client``) already measures blur, exposure, glare, and contrast.
This module is the single configuration surface for those thresholds, plus
the extreme-only short-circuit used on every review path *before* the
vision call.

Modes (``QUALITY_PREGATE_MODE``, default ``warn``):

* ``off``   — skip the gate; never block and never attach an advisory.
* ``warn``  — block only extreme cases (blank, black, or below minimum
  resolution). Moderate blur / dark / glare attach a ``quality`` advisory
  and the review continues.
* ``block`` — also short-circuit on the moderate ``diagnose_image_quality``
  floors (focus, lighting, glare, coverage).

Thresholds are conservative so a typical well-lit phone photo is never
blocked. Every value can be overridden with an environment variable of
the same name. Analysis runs on a downscaled working copy so a 12 MP
photo stays well under ~100 ms.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Literal, Optional

from PIL import Image

from .errors import RetakeCause, build_retake
from .models import ImageQualityAdvisory

_log = logging.getLogger(__name__)

PregateMode = Literal["off", "warn", "block"]

# ---------------------------------------------------------------------------
# Defaults (env overrides use the same names)
# ---------------------------------------------------------------------------

# Working-copy long edge for Laplacian / histogram work. 800 px keeps the
# empirical fixture thresholds meaningful and is cheap on 12 MP photos.
QUALITY_ANALYSIS_MAX_EDGE = 800

# Extreme floors — warn-mode short-circuit only. Mid-tone solid test images
# and ordinary dim/blurry field photos must not hit these.
EXTREME_MIN_SIDE = 80
EXTREME_MIN_PIXEL_AREA = 10_000  # 100x100; matches prepare_image
EXTREME_BLACK_MEAN = 8.0
EXTREME_BLANK_MEAN_HIGH = 248.0
EXTREME_BLANK_STDDEV = 2.0
EXTREME_GLARE_FRACTION = 0.92

# Moderate floors — advisory in warn mode; blocking in block mode.
# Values match the fixture-derived constants previously in claude_client.
# True OpenCV Laplacian (Pech-Pacheco). Pillow FIND_EDGES scored mild blur
# near 55 and severe near 20; the Laplacian scores those fixtures ~12 and ~1.
FOCUS_RETAKE_FLOOR = 8.0
DARK_FLOOR = 90.0
BRIGHT_CEILING = 235.0
GLARE_CLIP_FRACTION = 0.06
COVERAGE_MIN_STDDEV = 25.0

_DIAGNOSE_CAUSE_TO_RETAKE: dict[str, RetakeCause] = {
    "focus": "blur",
    "lighting_dark": "dark",
    "lighting_bright": "glare",
    "glare": "glare",
    "coverage": "low_resolution",
}

_CAUSE_WARNING: dict[str, str] = {
    "blur": "Photo looks out of focus.",
    "dark": "Photo looks too dark.",
    "glare": "Photo has glare or blown-out highlights.",
    "blank": "Photo appears blank; no label is visible in the frame.",
    "low_resolution": "Photo resolution or contrast is too low.",
}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        _log.warning("Invalid %s=%r; using default %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, float(default)))


def get_pregate_mode() -> PregateMode:
    raw = os.environ.get("QUALITY_PREGATE_MODE", "warn").strip().lower()
    if raw in ("off", "warn", "block"):
        return raw  # type: ignore[return-value]
    _log.warning("Invalid QUALITY_PREGATE_MODE=%r; using 'warn'", raw)
    return "warn"


@dataclass(frozen=True)
class QualityThresholds:
    """Resolved pre-gate thresholds for one assessment call."""

    mode: PregateMode
    analysis_max_edge: int
    extreme_min_side: int
    extreme_min_pixel_area: int
    extreme_black_mean: float
    extreme_blank_mean_high: float
    extreme_blank_stddev: float
    extreme_glare_fraction: float
    focus_retake_floor: float
    dark_floor: float
    bright_ceiling: float
    glare_clip_fraction: float
    coverage_min_stddev: float


def quality_thresholds() -> QualityThresholds:
    """Return thresholds with live environment overrides."""
    return QualityThresholds(
        mode=get_pregate_mode(),
        analysis_max_edge=max(64, _env_int("QUALITY_ANALYSIS_MAX_EDGE", QUALITY_ANALYSIS_MAX_EDGE)),
        extreme_min_side=max(1, _env_int("EXTREME_MIN_SIDE", EXTREME_MIN_SIDE)),
        extreme_min_pixel_area=max(1, _env_int("EXTREME_MIN_PIXEL_AREA", EXTREME_MIN_PIXEL_AREA)),
        extreme_black_mean=_env_float("EXTREME_BLACK_MEAN", EXTREME_BLACK_MEAN),
        extreme_blank_mean_high=_env_float("EXTREME_BLANK_MEAN_HIGH", EXTREME_BLANK_MEAN_HIGH),
        extreme_blank_stddev=_env_float("EXTREME_BLANK_STDDEV", EXTREME_BLANK_STDDEV),
        extreme_glare_fraction=_env_float("EXTREME_GLARE_FRACTION", EXTREME_GLARE_FRACTION),
        focus_retake_floor=_env_float("FOCUS_RETAKE_FLOOR", FOCUS_RETAKE_FLOOR),
        dark_floor=_env_float("DARK_FLOOR", DARK_FLOOR),
        bright_ceiling=_env_float("BRIGHT_CEILING", BRIGHT_CEILING),
        glare_clip_fraction=_env_float("GLARE_CLIP_FRACTION", GLARE_CLIP_FRACTION),
        coverage_min_stddev=_env_float("COVERAGE_MIN_STDDEV", COVERAGE_MIN_STDDEV),
    )


@dataclass
class QualityAssessment:
    """Result of the pre-gate for one photo (or a merged set)."""

    blur_score: float
    exposure: Literal["dark", "ok", "bright"]
    glare_fraction: float
    width: int
    height: int
    warnings: list[str] = field(default_factory=list)
    diagnose_cause: Optional[str] = None
    retake_cause: Optional[RetakeCause] = None
    should_block: bool = False
    block_cause: Optional[RetakeCause] = None

    def advisory(self) -> ImageQualityAdvisory:
        return ImageQualityAdvisory(
            blur_score=self.blur_score,
            exposure=self.exposure,
            glare_fraction=self.glare_fraction,
            resolution={"width": self.width, "height": self.height},
            warnings=list(self.warnings),
        )


def _exposure_label(brightness: float, thresholds: QualityThresholds) -> Literal["dark", "ok", "bright"]:
    if brightness < thresholds.dark_floor:
        return "dark"
    if brightness > thresholds.bright_ceiling:
        return "bright"
    return "ok"


def _analysis_copy(img: Image.Image, max_edge: int) -> Image.Image:
    width, height = img.size
    longest = max(width, height)
    if longest <= max_edge:
        return img
    scale = max_edge / longest
    # Bilinear is enough for histograms / Laplacian variance and is faster
    # than LANCZOS on a 12 MP working copy.
    return img.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.BILINEAR)


def _extreme_cause(
    width: int,
    height: int,
    brightness: float,
    contrast: float,
    glare: float,
    thresholds: QualityThresholds,
) -> Optional[RetakeCause]:
    if (
        width < thresholds.extreme_min_side
        or height < thresholds.extreme_min_side
        or width * height < thresholds.extreme_min_pixel_area
    ):
        return "low_resolution"
    if brightness < thresholds.extreme_black_mean:
        return "dark"
    # Solid-white / empty frames are blank, not glare. True specular
    # blow-out (high glare fraction without being a flat empty frame)
    # still maps to glare.
    blown_out = (
        contrast < thresholds.extreme_blank_stddev
        and brightness > thresholds.extreme_blank_mean_high
    )
    extreme_glare = (
        glare >= thresholds.extreme_glare_fraction
        and contrast < thresholds.extreme_blank_stddev
    )
    if blown_out:
        return "blank"
    if extreme_glare:
        return "glare"
    return None


def assess_image_quality(img: Image.Image) -> QualityAssessment:
    """Run ``diagnose_image_quality`` plus extreme checks on ``img``.

    Metrics are computed on a downscaled working copy; ``width`` / ``height``
    always reflect the original (already EXIF-transposed) image.
    """
    # Local import avoids a module-level cycle with claude_client.
    from .claude_client import diagnose_image_quality

    thresholds = quality_thresholds()
    width, height = img.size
    working = _analysis_copy(img, thresholds.analysis_max_edge)
    diagnosis = diagnose_image_quality(working)
    metrics = diagnosis.get("metrics") or {}
    blur_score = float(metrics.get("focus", 0.0))
    brightness = float(metrics.get("brightness", 0.0))
    contrast = float(metrics.get("contrast", 0.0))
    glare = float(metrics.get("glare_fraction", 0.0))

    diagnose_cause = diagnosis.get("cause")
    moderate_cause: Optional[RetakeCause] = None
    warnings: list[str] = []
    if diagnosis.get("ok") is False and diagnose_cause:
        moderate_cause = _DIAGNOSE_CAUSE_TO_RETAKE.get(str(diagnose_cause), "blur")
        warning = _CAUSE_WARNING.get(moderate_cause)
        if warning:
            warnings.append(warning)

    extreme_cause = _extreme_cause(width, height, brightness, contrast, glare, thresholds)
    if extreme_cause and _CAUSE_WARNING[extreme_cause] not in warnings:
        warnings.append(_CAUSE_WARNING[extreme_cause])

    mode = thresholds.mode
    should_block = False
    block_cause: Optional[RetakeCause] = None
    if mode == "off":
        warnings = []
    elif mode == "block":
        block_cause = extreme_cause or moderate_cause
        should_block = block_cause is not None
    else:
        block_cause = extreme_cause
        should_block = extreme_cause is not None

    retake_cause = extreme_cause or moderate_cause
    if mode == "off":
        retake_cause = None
        block_cause = None

    return QualityAssessment(
        blur_score=round(blur_score, 1),
        exposure=_exposure_label(brightness, thresholds),
        glare_fraction=round(glare, 3),
        width=width,
        height=height,
        warnings=warnings,
        diagnose_cause=str(diagnose_cause) if diagnose_cause else None,
        retake_cause=retake_cause,
        should_block=should_block,
        block_cause=block_cause,
    )


def merge_quality_assessments(
    assessments: list[QualityAssessment],
) -> Optional[QualityAssessment]:
    """Combine per-photo assessments into one advisory for the label."""
    if not assessments:
        return None
    worst_blur = min(assessments, key=lambda item: item.blur_score)
    most_glare = max(assessments, key=lambda item: item.glare_fraction)
    smallest = min(assessments, key=lambda item: item.width * item.height)
    warnings: list[str] = []
    for item in assessments:
        for warning in item.warnings:
            if warning not in warnings:
                warnings.append(warning)
    exposure: Literal["dark", "ok", "bright"] = "ok"
    if any(item.exposure == "dark" for item in assessments):
        exposure = "dark"
    elif any(item.exposure == "bright" for item in assessments):
        exposure = "bright"
    blocked = next((item for item in assessments if item.should_block), None)
    retake = next((item.retake_cause for item in assessments if item.retake_cause), None)
    return QualityAssessment(
        blur_score=worst_blur.blur_score,
        exposure=exposure,
        glare_fraction=most_glare.glare_fraction,
        width=smallest.width,
        height=smallest.height,
        warnings=warnings,
        diagnose_cause=(blocked.diagnose_cause if blocked else assessments[0].diagnose_cause),
        retake_cause=(blocked.block_cause if blocked else retake),
        should_block=blocked is not None,
        block_cause=blocked.block_cause if blocked else None,
    )


def quality_retake(cause: RetakeCause, *, tip: str | None = None):
    """Build the standard whole-image retake object for a pre-gate block."""
    return build_retake("label", cause, tip=tip)
