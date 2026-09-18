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


def get_required_min_height_mm(net_contents: Optional[str]) -> float:
    """Determine statutory 27 CFR 16.22 minimum capital letter height based on container capacity.

    Thresholds:
    - Containers <= 237 mL (8 fl. oz.): >= 1.0 mm
    - Containers > 237 mL to 3 L (101.4 fl. oz.): >= 2.0 mm
    - Containers > 3 L (101.4 fl. oz.): >= 3.0 mm
    - Unknown volume: default baseline 2.0 mm (standard retail container assumption)
    """
    if not net_contents:
        return 2.0

    # Import parser from compliance to share single source of truth
    from .compliance import _parse_net_contents

    qty, unit = _parse_net_contents(net_contents)
    if qty is None:
        return 2.0

    # Normalize to mL
    qty_ml: float
    if unit == "ml":
        qty_ml = qty
    elif unit == "floz":
        qty_ml = qty * 29.573535
    else:
        # Ambiguous unit: treat numbers >= 50 as mL, <= 10 as L
        if qty <= 10:
            qty_ml = qty * 1000.0
        else:
            qty_ml = qty

    if qty_ml <= 237.0:
        return 1.0
    elif qty_ml <= 3000.0:
        return 2.0
    else:
        return 3.0


def detect_id1_marker_contour(
    image_bgr: np.ndarray,
) -> Optional[Tuple[np.ndarray, float, float]]:
    """Detect an ISO/IEC 7810 ID-1 rectangular card in the image.

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
            # Card should occupy a reasonable fraction of image area (1% to 60%)
            if area < total_area * 0.01 or area > total_area * 0.70:
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

                # Allow up to 18% aspect ratio deviation for camera perspective
                if aspect_diff > 0.18:
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
) -> Optional[Tuple[float, float]]:
    """Locate Government Warning uppercase glyphs and calculate median capital letter height in pixels.

    Args:
        image_bgr: Full label image.
        gw_bbox: Optional normalized bounding box (x, y, width, height) in 0-1 relative coordinates.

    Returns:
        (median_cap_height_px, uncertainty_px) or None if glyphs cannot be segmented cleanly.
    """
    try:
        h_img, w_img = image_bgr.shape[:2]
        if h_img < 10 or w_img < 10:
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

        if crop.size == 0 or crop.shape[0] < 10 or crop.shape[1] < 10:
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

                if h < 6 or h > crop_h * 0.40:
                    continue
                if w < 2 or w > crop_w * 0.30:
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

        median_h = float(np.median(filtered))
        std_h = float(np.std(filtered)) if len(filtered) > 1 else median_h * 0.10
        uncertainty_px = float(1.96 * (std_h / math.sqrt(len(filtered))))

        return (median_h, max(uncertainty_px, median_h * 0.05))
    except Exception as exc:  # noqa: BLE001
        _log.debug("Glyph measurement exception: %s", exc)
        return None


def evaluate_type_size_from_image_bytes(
    image_bytes: bytes,
    filename: str = "label.jpg",
    net_contents: Optional[str] = None,
    gw_bbox: Optional[Tuple[float, float, float, float]] = None,
    calibration_hint: Optional[str] = None,
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

    try:
        if isinstance(image_bytes, (tuple, list)) and len(image_bytes) > 0:
            if len(image_bytes) > 1 and not filename and isinstance(image_bytes[1], str):
                filename = image_bytes[1]
            image_bytes = image_bytes[0]

        if not isinstance(image_bytes, (bytes, bytearray, memoryview)):
            return TypeSizeMeasurement(
                method=CalibrationMethod.NONE,
                required_min_height_mm=required_min_mm,
                state="cannot_measure",
                verification_note="Invalid image data received for type-size measurement.",
            )

        # Decode image to OpenCV BGR array
        np_arr = np.frombuffer(image_bytes, np.uint8)
        if np_arr.size == 0:
            return TypeSizeMeasurement(
                method=CalibrationMethod.NONE,
                required_min_height_mm=required_min_mm,
                state="cannot_measure",
                verification_note="Empty image data received for type-size measurement.",
            )
        image_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        if image_bgr is None or image_bgr.size == 0:
            return TypeSizeMeasurement(
                method=CalibrationMethod.NONE,
                required_min_height_mm=required_min_mm,
                state="cannot_measure",
                verification_note="Image could not be decoded for type-size measurement.",
            )

        # Step 1: Detect scale marker
        marker_res = detect_aruco_marker(image_bgr)
        method = CalibrationMethod.SCALE_MARKER_ARUCO

        if marker_res is None:
            marker_res = detect_id1_marker_contour(image_bgr)
            method = CalibrationMethod.SCALE_MARKER_CARD_ID1

        # If no physical marker is detected
        if marker_res is None:
            # Check if user explicitly asked for scale marker mode
            if calibration_hint in ("marker", "scale_marker", "card", "aruco"):
                return TypeSizeMeasurement(
                    method=CalibrationMethod.SCALE_MARKER_CARD_ID1,
                    required_min_height_mm=required_min_mm,
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
                state="cannot_measure",
                verification_note=(
                    f"Scale marker perspective tilt ({skew_angle:.1f}°) exceeds maximum 25.0° threshold. "
                    "Hold the camera parallel and flat-on to the label surface to avoid foreshortening errors, then retake the photo."
                ),
            )

        # Step 2: Segment capital letters in Government Warning
        glyph_res = measure_capital_letter_height_px(image_bgr, gw_bbox=gw_bbox)
        if glyph_res is None:
            return TypeSizeMeasurement(
                method=method,
                pixels_per_mm=round(px_per_mm, 2),
                skew_angle_deg=round(skew_angle, 1),
                required_min_height_mm=required_min_mm,
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

        return TypeSizeMeasurement(
            method=method,
            pixels_per_mm=round(px_per_mm, 2),
            measured_capital_height_mm=round(measured_cap_mm, 2),
            required_min_height_mm=required_min_mm,
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
            state="cannot_measure",
            verification_note=f"Type-size evaluation encountered an unexpected condition ({type(exc).__name__}). Physical gauge measurement recommended.",
        )
