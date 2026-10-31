"""Computer Vision module for 27 CFR 16.22 Type-Size Measurement (Option A Scale Marker).

This module provides deterministic CV algorithms (OpenCV / Pillow) to:
1. Detect scale markers (ISO/IEC 7810 ID-1 standard plastic card long edge 85.60 mm, ArUco fiducials, or millimeter rulers).
2. Compute spatial resolution (pixels per millimeter, px/mm) and planar skew/tilt angle.
3. Locate Government Warning text region, segment capital letter glyphs (e.g. 'G', 'O', 'V', 'E', 'R', 'N', 'M', 'E', 'N', 'T', 'W'), and compute median capital glyph height in millimeters.
4. Calculate measurement uncertainty (95% confidence interval half-width).
5. Output structured TypeSizeMeasurement data adhering to 27 CFR 16.22 compliance thresholds.

Sacred Invariants:
- Deterministic CV: no secondary LLM calls.
- Pessimistic safety: prefer cannot_measure / warning over false pass.
- Bounded latency: executes in < 100 ms on standard preprocessed label images.
- Fail-open & PII safety: card surface details are ignored or masked; only geometry/contours are analyzed.
"""

from __future__ import annotations

import io
import logging
import math
from typing import Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from .models import CalibrationMethod, TypeSizeMeasurement

_log = logging.getLogger(__name__)

# Standard ISO/IEC 7810 ID-1 card dimensions (e.g. standard credit card / ID card)
ID1_WIDTH_MM = 85.60
ID1_HEIGHT_MM = 53.98
ID1_ASPECT_RATIO = ID1_WIDTH_MM / ID1_HEIGHT_MM  # ~1.5858

# Maximum allowable perspective tilt angle in degrees before triggering cannot_measure / retake prompt
MAX_SKEW_ANGLE_DEG = 25.0

# ID-1 card detector: a real calibration card sits *beside* the label and is a
# modest fraction of the frame. An 18% aspect window plus a 70% area cap was
# treating the label rectangle itself as an 85.60 mm card (10 false cards on
# 33 card-less photos in the 2026-10-27 regression). Keep the card small,
# close to 1.5858, and never a quad that contains the Government Warning.
ID1_ASPECT_TOLERANCE = 0.06
ID1_MIN_AREA_FRACTION = 0.01
ID1_MAX_AREA_FRACTION = 0.30

# Capital-letter height: lowercase x-height glyphs dominate the median and
# under-read type size by ~23%. The 80th percentile tracks the cap-height
# cluster (prototype error about ±6%).
CAP_HEIGHT_PERCENTILE = 80.0

# Marker detection (bilateral + Canny + ArUco) is O(pixels). Full-resolution
# phone photos made the compliance stage seconds-long; 1280 px keeps ID-1
# cards and ArUco markers detectable and leaves millimetre ratios unchanged.
TYPESIZE_WORKING_MAX_EDGE = 1280

# Full-resolution glyph / crop gates. These are pixel counts, so they must
# be scaled when the working copy is smaller than the source photo.
GLYPH_MIN_HEIGHT_PX = 6
GLYPH_MIN_WIDTH_PX = 2
CROP_MIN_SIDE_PX = 10
IMAGE_MIN_SIDE_PX = 10


def _working_px_floor(base_px: int, scale: float) -> int:
    """Scale a full-res pixel gate when the working copy is smaller.

    After a 4000→1280 downscale a 2 mm capital can fall under 6 px and
    be dropped even though px/mm scaled by the same factor.
    """
    if scale >= 0.999:
        return base_px
    return max(1, int(math.floor(base_px * scale + 1e-9)))


def _working_copy_bgr(
    image_bgr: np.ndarray,
    max_edge: int = TYPESIZE_WORKING_MAX_EDGE,
) -> np.ndarray:
    """Area-resize so the long edge is at most ``max_edge`` pixels."""
    height, width = image_bgr.shape[:2]
    longest = max(height, width)
    if longest <= max_edge:
        return image_bgr
    scale = max_edge / longest
    new_w = max(1, int(width * scale))
    new_h = max(1, int(height * scale))
    return cv2.resize(image_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _decode_working_bgr(image_bytes: bytes) -> Tuple[Optional[np.ndarray], float]:
    """Decode toward TYPESIZE_WORKING_MAX_EDGE without a full-res bitmap.

    JPEG ``Image.draft`` uses MCU 1/2, 1/4, 1/8 scaling so a 12 MP phone
    photo never materializes at native size. OpenCV is the fallback for
    PNG/HEIC-reencoded bytes. Returns ``(bgr, scale)`` where ``scale`` is
    working-long-edge / original-long-edge (1.0 when not downscaled).
    px/mm ratios stay scale-invariant after ``_working_copy_bgr``.
    """
    if len(image_bytes) >= 3 and image_bytes[:2] == b"\xff\xd8":
        try:
            img = Image.open(io.BytesIO(image_bytes))
            orig_long = max(img.size) or 1
            if img.format == "JPEG":
                img.draft("RGB", (TYPESIZE_WORKING_MAX_EDGE, TYPESIZE_WORKING_MAX_EDGE))
            rgb = img.convert("RGB")
            rgb.load()
            working = _working_copy_bgr(cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR))
            work_long = max(working.shape[:2])
            return working, min(1.0, work_long / orig_long)
        except Exception:  # noqa: BLE001
            _log.debug("JPEG draft decode failed; falling back to OpenCV", exc_info=True)

    np_arr = np.frombuffer(image_bytes, np.uint8)
    if np_arr.size == 0:
        return None, 1.0
    image_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if image_bgr is None or image_bgr.size == 0:
        return None, 1.0
    orig_long = max(image_bgr.shape[:2]) or 1
    working = _working_copy_bgr(image_bgr)
    work_long = max(working.shape[:2])
    return working, min(1.0, work_long / orig_long)


def _container_volume_ml(net_contents: Optional[str]) -> Optional[float]:
    """Parse net contents into millilitres, or None when volume cannot be determined."""
    if not net_contents:
        return None

    from .compliance import _parse_net_contents

    qty, unit = _parse_net_contents(net_contents)
    if qty is None:
        return None

    if unit == "ml":
        return qty
    if unit == "floz":
        return qty * 29.573535
    if qty <= 10:
        return qty * 1000.0
    return qty


def get_required_min_height_mm(net_contents: Optional[str]) -> float:
    """Determine statutory 27 CFR 16.22(b) minimum capital letter height.

    Thresholds from eCFR 27 CFR 16.22(b):
    - Containers of 237 mL (8 fl. oz.) or less: not smaller than 1 mm
    - Containers of more than 237 mL up to 3 L: not smaller than 2 mm
    - Containers of more than 3 L: not smaller than 3 mm
    - Unknown volume: default baseline 2.0 mm (standard retail container assumption)
    """
    qty_ml = _container_volume_ml(net_contents)
    if qty_ml is None:
        return 2.0
    if qty_ml <= 237.0:
        return 1.0
    if qty_ml <= 3000.0:
        return 2.0
    return 3.0


def get_max_characters_per_inch(net_contents: Optional[str]) -> int:
    """Return the 27 CFR 16.22(a)(4) maximum characters per inch for the type-size tier.

    eCFR table (keyed to the minimum required type size):
    - 1 mm (237 mL or less): 40 characters per inch
    - 2 mm (more than 237 mL up to 3 L): 25 characters per inch
    - 3 mm (more than 3 L): 12 characters per inch
    """
    min_mm = get_required_min_height_mm(net_contents)
    if min_mm <= 1.0:
        return 40
    if min_mm <= 2.0:
        return 25
    return 12


def estimate_characters_per_inch(
    warning_text: Optional[str],
    bbox_width_px: float,
    bbox_height_px: float,
    pixels_per_mm: float,
    capital_height_mm: Optional[float] = None,
) -> Optional[float]:
    """Estimate 16.22(a)(4) characters per inch from a calibrated warning bbox.

    Counts letters, numbers, and marks (non-whitespace). Line count is inferred
    from bbox height versus capital height when available. Always an estimate.
    """
    if not warning_text or pixels_per_mm <= 0 or bbox_width_px <= 0:
        return None
    width_inches = (bbox_width_px / pixels_per_mm) / 25.4
    if width_inches < 0.05:
        return None
    import re as _re

    char_count = len(_re.sub(r"\s+", "", warning_text))
    if char_count == 0:
        return None
    if capital_height_mm and capital_height_mm > 0:
        cap_px = capital_height_mm * pixels_per_mm
        n_lines = max(1, int(round(bbox_height_px / max(cap_px * 1.35, 1.0))))
    else:
        n_lines = max(1, warning_text.count("\n") + 1)
    return (char_count / n_lines) / width_inches


def _warning_center_in_quad(
    ordered: np.ndarray,
    gw_bbox: Optional[Tuple[float, float, float, float]],
    image_width: int,
    image_height: int,
) -> bool:
    """True when the Government Warning centre lies inside the candidate card.

    A real ID-1 card is placed beside the label. The label rectangle itself
    often has a similar aspect ratio and would otherwise be accepted as a
    card, inventing a false millimetre scale.
    """
    if gw_bbox is None:
        return False
    gx, gy, gw, gh = gw_bbox
    cx = float((gx + gw / 2.0) * image_width)
    cy = float((gy + gh / 2.0) * image_height)
    contour = ordered.reshape(-1, 1, 2).astype(np.float32)
    return cv2.pointPolygonTest(contour, (cx, cy), False) >= 0


def detect_id1_marker_contour(
    image_bgr: np.ndarray,
    gw_bbox: Optional[Tuple[float, float, float, float]] = None,
    aspect_tolerance: float = ID1_ASPECT_TOLERANCE,
    max_area_fraction: float = ID1_MAX_AREA_FRACTION,
) -> Optional[Tuple[np.ndarray, float, float]]:
    """Detect an ISO/IEC 7810 ID-1 rectangular card in the image.

    The card must be a modest fraction of the frame, close to the statutory
    85.60 × 53.98 mm aspect, and must not contain the Government Warning
    text (the card sits beside the label, not around it).

    Returns:
        (ordered_quad_pts, pixels_per_mm, skew_angle_deg) or None if no valid marker detected.
    """
    try:
        h, w = image_bgr.shape[:2]
        total_area = w * h
        if total_area < 100:
            return None

        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        # Apply bilateral filter to smooth textures while preserving strong card edges
        blurred = cv2.bilateralFilter(gray, 7, 50, 50)

        # Edge detection
        edges = cv2.Canny(blurred, 40, 150)
        # Morphological close to join broken contour segments
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

        cnts_res = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours = cnts_res[0] if len(cnts_res) == 2 else cnts_res[1]

        best_marker: Optional[Tuple[np.ndarray, float, float]] = None
        best_score = 0.0

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < total_area * ID1_MIN_AREA_FRACTION or area > total_area * max_area_fraction:
                continue

            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.03 * peri, True)

            if len(approx) == 4 and cv2.isContourConvex(approx):
                pts = approx.reshape(4, 2).astype(np.float32)

                # Order points: top-left, top-right, bottom-right, bottom-left
                ordered = _order_quad_points(pts)

                # Compute edge lengths
                d_top = np.linalg.norm(ordered[0] - ordered[1])
                d_right = np.linalg.norm(ordered[1] - ordered[2])
                d_bottom = np.linalg.norm(ordered[2] - ordered[3])
                d_left = np.linalg.norm(ordered[3] - ordered[0])

                # Check parallel edge consistency
                width_avg = (d_top + d_bottom) / 2.0
                height_avg = (d_left + d_right) / 2.0

                if min(width_avg, height_avg) < 1e-4:
                    continue

                # Aspect ratio check (card can be landscape or portrait)
                aspect = max(width_avg, height_avg) / min(width_avg, height_avg)
                aspect_diff = abs(aspect - ID1_ASPECT_RATIO) / ID1_ASPECT_RATIO

                if aspect_diff > aspect_tolerance:
                    continue

                if _warning_center_in_quad(ordered, gw_bbox, w, h):
                    continue

                # Estimate skew / tilt angle from quadrilateral divergence
                skew_angle = _estimate_quad_skew_angle(ordered)

                # Pixels per mm calculated on long edge (85.60 mm)
                long_edge_px = max(width_avg, height_avg)
                px_per_mm = long_edge_px / ID1_WIDTH_MM

                # Score based on aspect match and area
                score = (1.0 - aspect_diff) * math.log(max(area, 1.0))
                if score > best_score:
                    best_score = score
                    best_marker = (ordered, px_per_mm, skew_angle)

        return best_marker
    except Exception as exc:  # noqa: BLE001
        _log.debug("ID-1 marker detection exception: %s", exc)
        return None


def detect_aruco_marker(
    image_bgr: np.ndarray,
    target_size_mm: float = 50.0,
) -> Optional[Tuple[np.ndarray, float, float]]:
    """Detect an ArUco fiducial calibration marker in the image.

    Returns:
        (ordered_quad_pts, pixels_per_mm, skew_angle_deg) or None if no marker detected.
    """
    if not hasattr(cv2, "aruco"):
        return None

    try:
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        # Try standard 4x4 or 6x6 dictionaries
        for dict_id in [cv2.aruco.DICT_4X4_50, cv2.aruco.DICT_6X6_50, cv2.aruco.DICT_APRILTAG_36h11 if hasattr(cv2.aruco, "DICT_APRILTAG_36h11") else None]:
            if dict_id is None:
                continue
            aruco_dict = cv2.aruco.getPredefinedDictionary(dict_id)
            if hasattr(cv2.aruco, "DetectorParameters"):
                parameters = cv2.aruco.DetectorParameters()
                if hasattr(cv2.aruco, "ArucoDetector"):
                    detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
                    corners, ids, _ = detector.detectMarkers(gray)
                else:
                    corners, ids, _ = cv2.aruco.detectMarkers(gray, aruco_dict, parameters=parameters)
            else:
                corners, ids, _ = cv2.aruco.detectMarkers(gray, aruco_dict)

            if ids is not None and len(corners) > 0:
                pts = corners[0].reshape(4, 2).astype(np.float32)
                ordered = _order_quad_points(pts)

                d_top = np.linalg.norm(ordered[0] - ordered[1])
                d_right = np.linalg.norm(ordered[1] - ordered[2])
                d_bottom = np.linalg.norm(ordered[2] - ordered[3])
                d_left = np.linalg.norm(ordered[3] - ordered[0])

                avg_side_px = (d_top + d_right + d_bottom + d_left) / 4.0
                px_per_mm = avg_side_px / target_size_mm
                skew_angle = _estimate_quad_skew_angle(ordered)

                return (ordered, px_per_mm, skew_angle)
    except Exception as exc:
        _log.debug("ArUco detection exception: %s", exc)

    return None


def _order_quad_points(pts: np.ndarray) -> np.ndarray:
    """Order 4 points: top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]

    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def _estimate_quad_skew_angle(ordered_pts: np.ndarray) -> float:
    """Estimate perspective planar skew angle (degrees) from quadrilateral edge parallelism."""
    try:
        v_top = ordered_pts[1] - ordered_pts[0]
        v_bottom = ordered_pts[2] - ordered_pts[3]
        v_left = ordered_pts[3] - ordered_pts[0]
        v_right = ordered_pts[2] - ordered_pts[1]

        len_top = float(np.linalg.norm(v_top))
        len_bottom = float(np.linalg.norm(v_bottom))
        len_left = float(np.linalg.norm(v_left))
        len_right = float(np.linalg.norm(v_right))

        if min(len_top, len_bottom, len_left, len_right) < 1e-4:
            return 90.0

        # Ratio divergence from trapezoidal perspective
        max_h = max(len_top, len_bottom)
        max_v = max(len_left, len_right)
        if max_h < 1e-4 or max_v < 1e-4:
            return 90.0

        ratio_h = abs(len_top - len_bottom) / max_h
        ratio_v = abs(len_left - len_right) / max_v

        # Perspective angle approximation: theta ~ arccos(1 - ratio) * (180 / pi)
        val_h = max(-1.0, min(1.0, 1.0 - ratio_h))
        val_v = max(-1.0, min(1.0, 1.0 - ratio_v))
        angle_h = math.degrees(math.acos(val_h))
        angle_v = math.degrees(math.acos(val_v))

        return max(angle_h, angle_v)
    except Exception as exc:  # noqa: BLE001
        _log.debug("Skew angle estimation exception: %s", exc)
        return 0.0


def measure_capital_letter_height_px(
    image_bgr: np.ndarray,
    gw_bbox: Optional[Tuple[float, float, float, float]] = None,
    scale: float = 1.0,
) -> Optional[Tuple[float, float]]:
    """Locate Government Warning uppercase glyphs and calculate capital letter height in pixels.

    Args:
        image_bgr: Full label image.
        gw_bbox: Optional normalized bounding box (x, y, width, height) in 0-1 relative coordinates.
        scale: working-copy / original long-edge ratio. Pixel gates shrink with it.

    Returns:
        (cap_height_px, uncertainty_px) or None if glyphs cannot be segmented cleanly.
        ``cap_height_px`` is the 80th percentile of candidate glyph heights.
    """
    min_image = _working_px_floor(IMAGE_MIN_SIDE_PX, scale)
    min_crop = _working_px_floor(CROP_MIN_SIDE_PX, scale)
    min_height = _working_px_floor(GLYPH_MIN_HEIGHT_PX, scale)
    min_width = _working_px_floor(GLYPH_MIN_WIDTH_PX, scale)
    try:
        h_img, w_img = image_bgr.shape[:2]
        if h_img < min_image or w_img < min_image:
            return None

        if gw_bbox is not None:
            gx, gy, gw, gh = gw_bbox
            # Add a 10% padding around bounding box
            x1 = max(0, int((gx - 0.05 * gw) * w_img))
            y1 = max(0, int((gy - 0.05 * gh) * h_img))
            x2 = min(w_img, int((gx + 1.05 * gw) * w_img))
            y2 = min(h_img, int((gy + 1.05 * gh) * h_img))
            if x2 <= x1 or y2 <= y1:
                return None
            crop = image_bgr[y1:y2, x1:x2]
        else:
            # If no bounding box provided, analyze middle-to-bottom band where warnings typically live
            y1 = int(h_img * 0.4)
            y2 = int(h_img * 0.95)
            x1 = int(w_img * 0.05)
            x2 = int(w_img * 0.95)
            if x2 <= x1 or y2 <= y1:
                return None
            crop = image_bgr[y1:y2, x1:x2]

        if crop.size == 0 or crop.shape[0] < min_crop or crop.shape[1] < min_crop:
            return None

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

        # Check contrast
        stat_std = float(np.std(gray))
        if stat_std < 15.0:
            # Low contrast / washed out region
            return None

        crop_h, crop_w = crop.shape[:2]
        best_glyphs: list[float] = []

        # Try both standard and inverted Otsu binarization to handle dark-on-light and light-on-dark labels
        thresh_modes = [cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU, cv2.THRESH_BINARY + cv2.THRESH_OTSU]
        if np.mean(gray) < 128:
            thresh_modes.reverse()

        for tmode in thresh_modes:
            _, thresh = cv2.threshold(gray, 0, 255, tmode)
            num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(thresh, connectivity=8)

            candidate_heights: list[float] = []
            for i in range(1, num_labels):
                x = stats[i, cv2.CC_STAT_LEFT]
                y = stats[i, cv2.CC_STAT_TOP]
                w = stats[i, cv2.CC_STAT_WIDTH]
                h = stats[i, cv2.CC_STAT_HEIGHT]
                area = stats[i, cv2.CC_STAT_AREA]

                if h < min_height or h > crop_h * 0.40:
                    continue
                if w < min_width or w > crop_w * 0.30:
                    continue

                aspect = w / float(h)
                if 0.25 <= aspect <= 1.40:
                    fill_ratio = area / float(w * h)
                    if 0.20 <= fill_ratio <= 0.85:
                        candidate_heights.append(float(h))

            if len(candidate_heights) > len(best_glyphs):
                best_glyphs = candidate_heights
                if len(best_glyphs) >= 8:
                    break

        if len(best_glyphs) < 4:
            # Too few candidate glyphs to produce statistically reliable measurement
            return None

        # Remove outlier glyphs (e.g. dots, accents, multi-line blobs)
        best_glyphs.sort()
        q25 = float(np.percentile(best_glyphs, 25))
        q75 = float(np.percentile(best_glyphs, 75))
        iqr = q75 - q25
        filtered = [gh for gh in best_glyphs if (q25 - 1.5 * iqr) <= gh <= (q75 + 1.5 * iqr)]

        if len(filtered) < 3:
            filtered = best_glyphs

        # 80th percentile tracks capital / ascender height; the median is
        # pulled down by lowercase x-height glyphs on mixed-case warnings.
        cap_h = float(np.percentile(filtered, CAP_HEIGHT_PERCENTILE))
        std_h = float(np.std(filtered)) if len(filtered) > 1 else cap_h * 0.10
        uncertainty_px = float(1.96 * (std_h / math.sqrt(len(filtered))))

        return (cap_h, max(uncertainty_px, cap_h * 0.05))
    except Exception as exc:  # noqa: BLE001
        _log.debug("Glyph measurement exception: %s", exc)
        return None


def _upscale_small(image_bgr: np.ndarray, min_long_edge: int = 1000) -> np.ndarray:
    """Cubic-upscale small photos so glyph segmentation has enough pixels."""
    height, width = image_bgr.shape[:2]
    longest = max(height, width)
    if longest >= min_long_edge:
        return image_bgr
    scale = min_long_edge / max(longest, 1)
    return cv2.resize(
        image_bgr,
        (max(1, int(width * scale)), max(1, int(height * scale))),
        interpolation=cv2.INTER_CUBIC,
    )


def _flatten_label_quad(image_bgr: np.ndarray) -> np.ndarray:
    """Warp the largest bright convex quad flat when it looks like a label.

    Skips the transform when the contour is almost the whole frame, too
    small, or not a stable quad — flattening hurt one shaded and one blurred
    photo in the 2026-10-27 prototype ablation.
    """
    height, width = image_bgr.shape[:2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return image_bgr
    contour = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(contour)
    total = float(width * height)
    if area < 0.20 * total or area > 0.97 * total:
        return image_bgr
    hull = cv2.convexHull(contour)
    approx = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
    if len(approx) != 4:
        return image_bgr
    pts = approx.reshape(4, 2).astype(np.float32)
    ordered = _order_quad_points(pts)
    dest_w = int(max(np.linalg.norm(ordered[0] - ordered[1]), np.linalg.norm(ordered[2] - ordered[3])))
    dest_h = int(max(np.linalg.norm(ordered[1] - ordered[2]), np.linalg.norm(ordered[3] - ordered[0])))
    if dest_w < 40 or dest_h < 40:
        return image_bgr
    destination = np.float32([[0, 0], [dest_w, 0], [dest_w, dest_h], [0, dest_h]])
    matrix = cv2.getPerspectiveTransform(ordered, destination)
    return cv2.warpPerspective(image_bgr, matrix, (dest_w, dest_h))


def _dewarp_cylinder(image_bgr: np.ndarray) -> np.ndarray:
    """Undo mild bottle-wrap fall-off when the brightness profile looks cylindrical."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    height, width = gray.shape
    if width < 40:
        return image_bgr
    profile = np.percentile(gray, 90, axis=0)
    centre = float(profile[int(width * 0.45) : int(width * 0.55)].mean())
    edge = float(
        (
            profile[int(width * 0.06) : int(width * 0.10)].mean()
            + profile[int(width * 0.90) : int(width * 0.94)].mean()
        )
        / 2.0
    )
    ratio = edge / max(centre, 1.0)
    # Flat labels and non-fall-off patterns stay untouched.
    if ratio > 0.93 or ratio < 0.30:
        return image_bgr
    theta_e = math.acos(min(max(ratio, 0.0), 1.0))
    half = theta_e / 0.92 if theta_e < 1.4 else 1.5
    half = min(half, 1.5)
    radius = (width / 2.0) / math.sin(half)
    out_w = int(2 * radius * half)
    if out_w < 40:
        return image_bgr
    xs = (np.arange(out_w) - out_w / 2.0) / radius
    src = (np.sin(np.clip(xs, -half, half)) * radius + width / 2.0).astype(np.float32)
    map_x = np.tile(src, (height, 1))
    map_y = np.tile(np.arange(height, dtype=np.float32)[:, None], (1, out_w))
    out = cv2.remap(image_bgr, map_x, map_y, cv2.INTER_CUBIC)
    shade = np.cos(np.clip(xs, -half, half))[None, :, None]
    return np.clip(out / np.maximum(shade, 0.35), 0, 255).astype(np.uint8)


def apply_guarded_geometry(image_bgr: np.ndarray) -> np.ndarray:
    """Upscale, flatten, and dewarp when those steps look safe.

    Global CLAHE is intentionally omitted: it destroyed OCR recall on noisy
    and JPEG-compressed photos in the 2026-10-27 ablation. Each step fails
    open and returns the previous image on error.
    """
    working = image_bgr
    for step in (_upscale_small, _flatten_label_quad, _dewarp_cylinder):
        try:
            next_img = step(working)
        except Exception as exc:  # noqa: BLE001
            _log.debug("Guarded geometry step %s skipped: %s", step.__name__, exc)
            continue
        if next_img is None or next_img.size == 0:
            continue
        working = next_img
    return working


def evaluate_type_size_from_image_bytes(
    image_bytes: bytes,
    filename: str = "label.jpg",
    net_contents: Optional[str] = None,
    gw_bbox: Optional[Tuple[float, float, float, float]] = None,
    calibration_hint: Optional[str] = None,
    warning_text: Optional[str] = None,
) -> TypeSizeMeasurement:
    """Main entry point for 27 CFR 16.22 Type-Size Verification.

    Args:
        image_bytes: Raw or preprocessed image bytes.
        filename: Name of the uploaded photo.
        net_contents: Extracted net contents string (e.g. '750 mL') for statutory volume tiering.
        gw_bbox: Optional normalized (x, y, w, h) bounding box of Government Warning.
        calibration_hint: Optional hint ('marker', 'card', 'aruco', 'geometry', 'uncalibrated').

    Returns:
        TypeSizeMeasurement model with calibrated dimensions, uncertainty bounds, state, and audit notes.
    """
    required_min_mm = get_required_min_height_mm(net_contents)
    max_cpi = get_max_characters_per_inch(net_contents)

    try:
        if not image_bytes:
            return TypeSizeMeasurement(
                method=CalibrationMethod.NONE,
                required_min_height_mm=required_min_mm,
                max_characters_per_inch=max_cpi,
                state="cannot_measure",
                verification_note="Empty image data received for type-size measurement.",
            )
        # Draft-decode JPEGs toward the working max edge. px/mm and glyph
        # height scale together, so millimetre results stay the same.
        image_bgr, working_scale = _decode_working_bgr(image_bytes)

        if image_bgr is None or image_bgr.size == 0:
            return TypeSizeMeasurement(
                method=CalibrationMethod.NONE,
                required_min_height_mm=required_min_mm,
                max_characters_per_inch=max_cpi,
                state="cannot_measure",
                verification_note="Image could not be decoded for type-size measurement.",
            )

        # Uniform upscale only on the full frame. Flatten/dewarp crop away a
        # card placed beside the label and invalidate gw_bbox coordinates.
        try:
            image_bgr = _upscale_small(image_bgr)
        except Exception as exc:  # noqa: BLE001
            _log.debug("Type-size upscale skipped: %s", exc)

        # Step 1: Detect scale marker in the original (upscaled) frame so
        # gw_bbox still matches the pixels used for warning-in-quad rejection.
        marker_res = detect_aruco_marker(image_bgr)
        method = CalibrationMethod.SCALE_MARKER_ARUCO

        if marker_res is None:
            marker_res = detect_id1_marker_contour(image_bgr, gw_bbox=gw_bbox)
            method = CalibrationMethod.SCALE_MARKER_CARD_ID1

        # If no physical marker is detected
        if marker_res is None:
            # Check if user explicitly asked for scale marker mode
            if calibration_hint in ("marker", "scale_marker", "card", "aruco"):
                return TypeSizeMeasurement(
                    method=CalibrationMethod.SCALE_MARKER_CARD_ID1,
                    required_min_height_mm=required_min_mm,
                max_characters_per_inch=max_cpi,
                    state="cannot_measure",
                    verification_note=(
                        "Scale marker not detected in photo. Please place a standard ISO/IEC 7810 ID-1 card "
                        "(e.g. driver's license, credit card) or calibrated marker beside the Government Warning and retake the photo."
                    ),
                )
            # Uncalibrated photo path: retain honest advisory without invented mm
            return TypeSizeMeasurement(
                method=CalibrationMethod.UNCALIBRATED_ESTIMATE,
                required_min_height_mm=required_min_mm,
                max_characters_per_inch=max_cpi,
                state="warning",
                verification_note=(
                    f"Uncalibrated photo without physical scale marker. Statutory threshold is >={required_min_mm:.1f} mm "
                    f"based on container volume ({net_contents or 'standard'}). Physical gauge measurement required to verify 27 CFR 16.22 compliance."
                ),
            )

        ordered_pts, px_per_mm, skew_angle = marker_res

        # Check for excessive perspective tilt / skew (>25 degrees)
        if skew_angle > MAX_SKEW_ANGLE_DEG:
            return TypeSizeMeasurement(
                method=method,
                pixels_per_mm=round(px_per_mm, 2),
                skew_angle_deg=round(skew_angle, 1),
                required_min_height_mm=required_min_mm,
                max_characters_per_inch=max_cpi,
                state="cannot_measure",
                verification_note=(
                    f"Scale marker perspective tilt ({skew_angle:.1f}°) exceeds maximum 25.0° threshold. "
                    "Hold the camera parallel and flat-on to the label surface to avoid foreshortening errors, then retake the photo."
                ),
            )

        # Step 2: Segment capital letters in Government Warning.
        # Measure on the same frame used for marker detection. Flattening the
        # full image first would crop a card beside the label and invalidate
        # gw_bbox. Flattening or upscaling a warning crop would change pixel
        # scale versus the px/mm computed from the marker.
        glyph_res = measure_capital_letter_height_px(
            image_bgr, gw_bbox=gw_bbox, scale=working_scale
        )
        if glyph_res is None:
            return TypeSizeMeasurement(
                method=method,
                pixels_per_mm=round(px_per_mm, 2),
                skew_angle_deg=round(skew_angle, 1),
                required_min_height_mm=required_min_mm,
                max_characters_per_inch=max_cpi,
                state="cannot_measure",
                verification_note=(
                    "Scale marker detected, but Government Warning text glyphs could not be segmented cleanly. "
                    "Ensure the warning text is in sharp focus and free of glare or shadows, then retake the photo."
                ),
            )

        median_cap_px, uncertainty_px = glyph_res
        if px_per_mm <= 0:
            return TypeSizeMeasurement(
                method=method,
                required_min_height_mm=required_min_mm,
                max_characters_per_inch=max_cpi,
                state="cannot_measure",
                verification_note="Invalid spatial resolution calculated from scale marker.",
            )

        measured_cap_mm = median_cap_px / px_per_mm
        uncertainty_mm = uncertainty_px / px_per_mm

        # Pessimistic lower bound for pass determination: measured_mm - uncertainty_mm
        # Pessimistic upper bound for fail determination: measured_mm + uncertainty_mm
        lower_bound_mm = measured_cap_mm - uncertainty_mm
        upper_bound_mm = measured_cap_mm + uncertainty_mm

        # Step 3: Compare against 16.22 statutory threshold
        if lower_bound_mm >= required_min_mm:
            state = "pass"
            verification_note = (
                f"Calibrated capital letter height {measured_cap_mm:.2f} mm ± {uncertainty_mm:.2f} mm "
                f"meets or exceeds statutory 27 CFR 16.22 requirement ({required_min_mm:.1f} mm) for container volume ({net_contents or 'standard'})."
            )
        elif upper_bound_mm < required_min_mm:
            state = "fail"
            verification_note = (
                f"Calibrated capital letter height {measured_cap_mm:.2f} mm ± {uncertainty_mm:.2f} mm "
                f"is below the statutory 27 CFR 16.22 requirement ({required_min_mm:.1f} mm) for container volume ({net_contents or 'standard'})."
            )
        else:
            # Marginal case within uncertainty window
            state = "warning"
            verification_note = (
                f"Calibrated capital letter height {measured_cap_mm:.2f} mm ± {uncertainty_mm:.2f} mm "
                f"is borderline near the {required_min_mm:.1f} mm threshold. Physical gauge verification recommended."
            )

        estimated_cpi = None
        if warning_text and gw_bbox is not None:
            h_img, w_img = image_bgr.shape[:2]
            bbox_w_px = gw_bbox[2] * w_img
            bbox_h_px = gw_bbox[3] * h_img
            estimated_cpi = estimate_characters_per_inch(
                warning_text,
                bbox_w_px,
                bbox_h_px,
                px_per_mm,
                measured_cap_mm,
            )

        return TypeSizeMeasurement(
            method=method,
            pixels_per_mm=round(px_per_mm, 2),
            measured_capital_height_mm=round(measured_cap_mm, 2),
            required_min_height_mm=required_min_mm,
            max_characters_per_inch=max_cpi,
            estimated_characters_per_inch=(
                round(estimated_cpi, 1) if estimated_cpi is not None else None
            ),
            uncertainty_mm=round(uncertainty_mm, 2),
            skew_angle_deg=round(skew_angle, 1),
            state=state,
            verification_note=verification_note,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("Uncaught exception in evaluate_type_size_from_image_bytes for '%s': %s", filename, exc)
        return TypeSizeMeasurement(
            method=CalibrationMethod.UNCALIBRATED_ESTIMATE,
            required_min_height_mm=required_min_mm,
            max_characters_per_inch=max_cpi,
            state="cannot_measure",
            verification_note=f"Type-size evaluation encountered an unexpected condition ({type(exc).__name__}). Physical gauge measurement recommended.",
        )
