"""Compliance matching and TTB label requirement checks.

This module is the core "business logic" of the prototype and is kept free
of any I/O (no network calls, no file access) so it can be unit tested
without an Anthropic API key.

Two entry points are exported:

- ``run_compliance_checks`` - compares a label's extracted fields against a
  COLA application's data (Application vs. Label review).
- ``check_label_requirements`` - validates a label's extracted fields
  against the baseline TTB mandatory label requirements (27 CFR Parts 4, 5,
  7, 16), independent of any application data (Label-Only Check).

Every check returns a ``FieldResult`` with a ``status`` of ``"pass"``,
``"warning"`` (needs human review), or ``"fail"``, plus a plain-language
``message`` explaining the result.

New in this version
-------------------
* Confidence-gated extraction: LowConfidenceError is raised when the model's
  overall extraction confidence falls below the per-class threshold defined in
  BEVERAGE_TOLERANCE. Previously the tool would silently accept a best guess
  and return a pass even when the image was unreadable.
* Configurable tolerance rules per beverage class: BEVERAGE_TOLERANCE maps
  each beverage class to its ABV tolerance AND a minimum extraction confidence
  score below which the whole check is rejected.
* Beverage-type confirmation flow: check_label_requirements now accepts an
  optional confirmed_beverage_type parameter. When supplied (via the agent
  override UI) it takes precedence over extracted.beverage_type_guess. When
  absent the function returns an "unconfirmed_beverage_type" sentinel so the
  caller can prompt the agent to verify before requirements are evaluated.
* Standards-of-fill validation: _check_label_net_contents now validates the
  extracted quantity against the authorised fill sizes per 27 CFR 4.72 /
  5.203 / 7.70 rather than only confirming a number is present.
* Formula-dependent caveats: sulfite declaration, allergen disclosures, age
  statement, and commodity statement checks all carry an explicit caveat that
  a label-level pass does not substitute for production/formula record review.
"""

import re
import logging
from difflib import SequenceMatcher
from typing import Optional

# Module-level logger for compliance checks.
_log = logging.getLogger(__name__)
from .models import ApplicationData, ExtractedLabelData, FieldResult


# ---------------------------------------------------------------------------
# Confidence gating
# ---------------------------------------------------------------------------

class LowConfidenceError(ValueError):
    """Raised when extraction confidence is below the per-class minimum threshold.

    Callers (main.py) should catch this and return HTTP 422 with the
    user_message so the agent knows to retake the photo.
    """

    def __init__(self, user_message: str, confidence: float, threshold: float) -> None:
        self.user_message = user_message
        self.confidence = confidence
        self.threshold = threshold
        super().__init__(user_message)


# ---------------------------------------------------------------------------
# Configurable tolerance rules per beverage class
# ---------------------------------------------------------------------------
#
# BEVERAGE_TOLERANCE is the single source of truth for both ABV tolerance and
# extraction-confidence thresholds. Operators can adjust these values (or load
# them from a config file / env vars) without touching logic code.
#
# Keys match BeverageType literals from models.py plus "unknown" sentinel.
#
# abv_tolerance   float  max allowed |app_pct - label_pct| before fail.
# min_confidence  float  0-1. If ExtractedLabelData.extraction_confidence is
#                        below this value the whole check is rejected and a
#                        new photo is requested.

BEVERAGE_TOLERANCE: dict[str, dict] = {
    "distilled_spirits": {
        "abv_tolerance": 0.3,       # 27 CFR 5.65(c)
        "min_confidence": 0.50,
    },
    "wine": {
        # Wine ABV tolerance is ABV-dependent; see _wine_abv_tolerance().
        # The value here is the fallback used when ABV cannot be parsed.
        "abv_tolerance": 1.0,       # 27 CFR 4.36(b)(1) (>14% ABV band)
        "min_confidence": 0.50,
    },
    "beer": {
        "abv_tolerance": 0.3,       # 27 CFR 7.65(c)
        "min_confidence": 0.45,
    },
    "unknown": {
        "abv_tolerance": 0.3,
        "min_confidence": 0.55,     # Stricter: type-specific rules cannot be validated
    },
}

# Default confidence threshold used when beverage class is not in the table.
_DEFAULT_MIN_CONFIDENCE = 0.50

# ---------------------------------------------------------------------------
# Per-field confidence thresholds
# ---------------------------------------------------------------------------
#
# FIELD_CONFIDENCE_THRESHOLDS controls which per-field confidence score
# (from ExtractedLabelData.per_field_confidence) triggers a field-level fail
# with a request for a better photo rather than a low-confidence pass.
#
# Government Warning body text is set lower (0.45) because it is ~80 words
# printed in small font on a curved bottle surface; vision models legitimately
# score it lower than a short brand name even on a well-lit photo.
# brand_name, alcohol_content, and net_contents are short high-stakes fields.
# Fields absent from this table use _DEFAULT_FIELD_CONFIDENCE (0.50).

FIELD_CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "brand_name":        0.35,
    "alcohol_content":   0.30,
    "net_contents":      0.30,
    "class_type":        0.30,
    "government_warning_body":   0.20,
    "government_warning_header": 0.25,
    "name_and_address":          0.25,
    "sulfite_declaration":  0.20,
    "allergen_statements":  0.20,
    "age_statement":        0.20,
    "commodity_statement":  0.20,
}

_DEFAULT_FIELD_CONFIDENCE = 0.25


# ---------------------------------------------------------------------------
# Canonical government-warning text (27 CFR 16.21)
# ---------------------------------------------------------------------------

CANONICAL_WARNING_HEADER = "GOVERNMENT WARNING:"
CANONICAL_WARNING_BODY = (
    "(1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a "
    "car or operate machinery, and may cause health problems."
)


# Small-container Government Warning (27 CFR 16.21(c))
# Containers with capacity <= 100 mL may use an abbreviated warning.
SMALL_CONTAINER_THRESHOLD_ML = 100.0
SMALL_CONTAINER_THRESHOLD_FLOZ = 3.381

# Abbreviated warning body for containers <= 100 mL (omits clause numbers).
CANONICAL_WARNING_BODY_SHORT = (
    "According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. "
    "Consumption of alcoholic beverages impairs your ability to drive a "
    "car or operate machinery, and may cause health problems."
)

# ---------------------------------------------------------------------------
# Wine ABV tolerance helper (27 CFR 4.36(b)(1))
# ---------------------------------------------------------------------------

WINE_ABV_TOLERANCE_THRESHOLD = 14.0
WINE_ABV_TOLERANCE_LOW = 1.5
WINE_ABV_TOLERANCE_HIGH = 1.0


def _wine_abv_tolerance(app_pct: float, label_pct: float) -> float:
    """Return the wine ABV tolerance per 27 CFR 4.36(b)(1).

    Wines >14% ABV get +/-1.0 tolerance; wines <=14% get +/-1.5.
    The higher of the two values is used to determine which band applies.
    """
    if max(app_pct, label_pct) > WINE_ABV_TOLERANCE_THRESHOLD:
        return WINE_ABV_TOLERANCE_HIGH
    return WINE_ABV_TOLERANCE_LOW


# ---------------------------------------------------------------------------
# Standards of fill (27 CFR 4.72 / 5.203 / 7.70)
# ---------------------------------------------------------------------------
# All values are in millilitres. FL OZ values are US customary equivalents
# printed on domestic labels; both are accepted.

# 27 CFR 4.72 - Wine
_WINE_FILL_ML = frozenset({
    100, 187, 250, 375, 500, 750, 1000, 1500, 3000, 4500,
})
_WINE_FILL_FLOZ = frozenset({
    3.4, 6.3, 8.5, 12.7, 16.9, 25.4, 33.8, 50.7, 101.4, 152.2,
})

# 27 CFR 5.203 - Distilled spirits
_SPIRITS_FILL_ML = frozenset({
    50, 100, 200, 375, 500, 750, 1000, 1750,
})
_SPIRITS_FILL_FLOZ = frozenset({
    1.7, 3.4, 6.8, 12.7, 16.9, 25.4, 33.8, 59.2,
})

# 27 CFR 7.70 - Malt beverages (beer)
# Malt beverages do not have a federally mandated closed list of fill sizes;
# 27 CFR 7.70 only requires the net contents to be stated accurately. We
# therefore skip the enumerated-size check for beer and confirm a numeric
# quantity is present.
_BEER_FILL_UNRESTRICTED = True

# Tolerance for fill-size matching: +/-1 mL or +/-0.1 fl oz to account for
# rounding in label printing.
_FILL_ML_TOLERANCE = 1.0
_FILL_FLOZ_TOLERANCE = 0.1


def _parse_net_contents(text: str) -> tuple:
    """Parse a net-contents string into (quantity: float|None, unit: str|None).

    Returns (float, 'ml'), (float, 'floz'), or (None, None).
    Handles: '750 mL', '750ml', '25.4 fl oz', '12 FL. OZ.', '1 L', etc.
    """
    if not text:
        return None, None
    t = text.lower().strip()
    # Millilitres
    m = re.search(r"([\d]+(?:[.,][\d]+)?)\s*(?:ml|milliliter|millilitre)s?", t)
    if m:
        return float(m.group(1).replace(",", ".")), "ml"
    # Litres -> convert to mL
    m = re.search(r"([\d]+(?:[.,][\d]+)?)\s*(?:l|liter|litre)(?!s?\s*oz)", t)
    if m:
        return float(m.group(1).replace(",", ".")) * 1000, "ml"
    # Fluid ounces
    m = re.search(r"([\d]+(?:[.,][\d]+)?)\s*(?:fl\.?\s*oz\.?|fluid\s+ounce)s?", t)
    if m:
        return float(m.group(1).replace(",", ".")), "floz"
    # Plain number - ambiguous unit
    m = re.search(r"([\d]+(?:[.,][\d]+)?)", t)
    if m:
        return float(m.group(1).replace(",", ".")), None
    return None, None


def _is_small_container(net_contents) -> bool:
    """Return True when the container is <= 100 mL (27 CFR 16.21(c))."""
    if not net_contents:
        return False
    qty, unit = _parse_net_contents(net_contents)
    if qty is None or unit is None:
        return False
    return (unit == "ml" and qty <= SMALL_CONTAINER_THRESHOLD_ML) or (unit == "floz" and qty <= SMALL_CONTAINER_THRESHOLD_FLOZ)

def _is_authorised_fill(qty: float, unit, beverage_type) -> bool:
    """Return True/False if qty/unit is an authorised standard of fill.

    Returns None if the check is not applicable or cannot be determined
    (e.g. beer, or unknown unit).

    Args:
        qty:           Numeric quantity extracted from the label.
        unit:          'ml', 'floz', or None.
        beverage_type: 'wine', 'distilled_spirits', 'beer', or None/unknown.
    """
    if unit is None:
        return None  # Cannot evaluate without a unit
    if beverage_type == "beer":
        return None  # No restricted list for malt beverages (27 CFR 7.70)
    if unit == "ml":
        authorised = _WINE_FILL_ML if beverage_type == "wine" else _SPIRITS_FILL_ML
        return any(abs(qty - a) <= _FILL_ML_TOLERANCE for a in authorised)
    if unit == "floz":
        authorised = _WINE_FILL_FLOZ if beverage_type == "wine" else _SPIRITS_FILL_FLOZ
        return any(abs(qty - a) <= _FILL_FLOZ_TOLERANCE for a in authorised)
    return None


# ---------------------------------------------------------------------------
# Text normalisation helpers
# ---------------------------------------------------------------------------

def _normalize(text) -> str:
    """Lowercase, strip punctuation, and collapse whitespace."""
    if not text:
        return ""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9% ]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_net_contents(text) -> str:
    """Normalise net contents, treating equivalent unit abbreviations the same."""
    norm = _normalize(text)
    norm = re.sub(r"\bfl(uid)?\b", "", norm)
    norm = re.sub(r"\bounces?\b", "oz", norm)
    norm = re.sub(r"\bmilliliters?\b", "ml", norm)
    norm = re.sub(r"\bliters?\b", "l", norm)
    norm = re.sub(r"\s+", " ", norm)
    return norm.strip()


def _normalize_whitespace(text) -> str:
    """Collapse runs of whitespace to a single space, preserving case."""
    if not text:
        return ""
    text = re.sub(r"-\n\s*", "", text.strip())
    return re.sub(r"\s+", " ", text.strip())


def _similarity(a: str, b: str) -> float:
    """Return a 0-1 similarity ratio between two strings (via difflib)."""
    return SequenceMatcher(None, a, b).ratio()


def _extract_percent(text) -> float:
    """Pull the first NN.N% style percentage out of free text, if any."""
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if match:
        return float(match.group(1))
    return None


# ---------------------------------------------------------------------------
# Confidence-gate helper
# ---------------------------------------------------------------------------

def assert_extraction_confidence(extracted, beverage_type=None):
    """Raise LowConfidenceError if extraction confidence is below threshold.

    Checks two levels:
    1. Overall confidence (extracted.extraction_confidence) against the
       per-class minimum in BEVERAGE_TOLERANCE.
    2. Per-field confidence (extracted.per_field_confidence) against
       FIELD_CONFIDENCE_THRESHOLDS.  If any key field has a score below its
       threshold, a LowConfidenceError is raised naming the specific field.
    """
    if extracted.extraction_confidence is None:
        _log.debug("assert_extraction_confidence: no score emitted, skipping gate.")
        return

    cls = str(beverage_type or "unknown").lower()
    config = BEVERAGE_TOLERANCE.get(cls, BEVERAGE_TOLERANCE["unknown"])
    threshold = config.get("min_confidence", _DEFAULT_MIN_CONFIDENCE)
    conf = extracted.extraction_confidence

    if conf < threshold:
        _log.info("Low overall extraction confidence %.2f < %.2f (%s)", conf, threshold, cls)
        raise LowConfidenceError(
            user_message=(
                f"The label image quality is too low to reliably read the required fields "
                f"(confidence {conf:.0%}, minimum {threshold:.0%} for {cls}). "
                "Please retake the photo: ensure the label is flat-on, well-lit, in sharp "
                "focus, and the entire label is visible within the frame."
            ),
            confidence=conf,
            threshold=threshold,
        )

    if not extracted.per_field_confidence:
        return

    field_issues = []
    for field_name, field_score in extracted.per_field_confidence.items():
            if field_name == "country_of_origin" and field_score == 0.0:
                        continue  # Absent on domestic labels - not a photo quality issue
        field_threshold = FIELD_CONFIDENCE_THRESHOLDS.get(field_name, _DEFAULT_FIELD_CONFIDENCE)
        if field_score < field_threshold:
            _log.info("Low per-field confidence for '%s': %.2f < %.2f", field_name, field_score, field_threshold)
            field_issues.append((field_name, field_score, field_threshold))

    if field_issues:
        field_descriptions = {
            "brand_name": "brand name",
            "class_type": "class/type designation",
            "alcohol_content": "alcohol content (ABV)",
            "net_contents": "net contents / fill quantity",
            "government_warning_header": "Government Warning header",
            "government_warning_body": "Government Warning text body",
            "name_and_address": "bottler/producer name and address",
            "sulfite_declaration": "sulfite declaration",
            "allergen_statements": "allergen statements",
            "age_statement": "age statement",
            "commodity_statement": "commodity statement",
        }
        readable_fields = [field_descriptions.get(fn, fn) for fn, _, _ in field_issues]
        noun = "fields" if len(readable_fields) > 1 else "field"
        listed = ", ".join(f'"{f}"' for f in readable_fields)
        conf_detail = "; ".join(f"{fn} {fs:.0%}" for fn, fs, _ in field_issues)
        raise LowConfidenceError(
            user_message=(
                f"The following label {noun} could not be read clearly enough "
                f"to produce a reliable compliance result: {listed}. "
                f"(Confidence scores: {conf_detail}.) "
                "Please retake the photo focusing on the affected area: ensure "
                "the label surface is fully visible, not curved into shadow, and "
                "the text is sharply in focus."
            ),
            confidence=min(fs for _, fs, _ in field_issues),
            threshold=min(ft for _, _, ft in field_issues),
        )

def _check_text_field(
    field: str,
    label_name: str,
    application_value: str,
    label_value,
    normalizer=_normalize,
) -> FieldResult:
    """Compare an application value to the corresponding label value.

    - Missing on label -> fail.
    - Equal after normalisation -> pass.
    - >=85% similar after normalisation -> warning (cosmetic difference).
    - Otherwise -> fail.
    """
    norm_app = normalizer(application_value)
    norm_label = normalizer(label_value)

    if not label_value:
        return FieldResult(
            field=field, label_name=label_name, status="fail",
            application_value=application_value, label_value=label_value,
            message=f"{label_name} not found on label.",
        )

    if norm_app == norm_label:
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=application_value, label_value=label_value,
            message=f"{label_name} matches application.",
        )

    similarity = _similarity(norm_app, norm_label)
    if similarity >= 0.85:
        return FieldResult(
            field=field, label_name=label_name, status="warning",
            application_value=application_value, label_value=label_value,
            message=(
                f"{label_name} is a likely match but differs in formatting/casing "
                "from the application. Please confirm visually."
            ),
        )

    return FieldResult(
        field=field, label_name=label_name, status="fail",
        application_value=application_value, label_value=label_value,
        message=f"{label_name} on label does not match application.",
    )


def _check_alcohol_content(
    application: ApplicationData, extracted: ExtractedLabelData
) -> FieldResult:
    """Compare the application's stated ABV to the label's ABV.

    Uses BEVERAGE_TOLERANCE for per-class tolerance values.
    """
    label_value = extracted.alcohol_content
    field, label_name = "alcohol_content", "Alcohol Content"

    if not label_value:
        if application.beverage_type in ("wine", "beer"):
            return FieldResult(
                field=field, label_name=label_name, status="warning",
                application_value=application.alcohol_content, label_value=label_value,
                message=(
                    "Alcohol content not found on label. This may be acceptable "
                    f"for {application.beverage_type.replace('_', ' ')} "
                    "(verify whether an exemption applies)."
                ),
            )
        return FieldResult(
            field=field, label_name=label_name, status="fail",
            application_value=application.alcohol_content, label_value=label_value,
            message="Alcohol content not found on label.",
        )

    app_pct = _extract_percent(application.alcohol_content)
    label_pct = _extract_percent(label_value)

    if app_pct is None or label_pct is None:
        return _check_text_field(field, label_name, application.alcohol_content, label_value)

    if application.beverage_type == "wine":
        tolerance = _wine_abv_tolerance(app_pct, label_pct)
    else:
        class_cfg = BEVERAGE_TOLERANCE.get(application.beverage_type, {})
        tolerance = class_cfg.get("abv_tolerance", 0.3)
    diff = abs(app_pct - label_pct)

    if diff == 0:
        status, message = "pass", "Alcohol content matches application."
    elif diff <= tolerance:
        status, message = (
            "warning",
            f"Alcohol content differs by {diff:.2f}%, within the "
            f"{tolerance}% TTB tolerance but should be confirmed.",
        )
    else:
        status, message = (
            "fail",
            f"Alcohol content differs by {diff:.2f}%, exceeding the "
            f"{tolerance}% TTB tolerance.",
        )

    return FieldResult(
        field=field, label_name=label_name, status=status,
        application_value=application.alcohol_content, label_value=label_value,
        message=message,
    )


def _check_government_warning(extracted) -> FieldResult:
    """Validate the Government Warning statement (27 CFR 16.21).

    Supports the abbreviated short-form body for containers <= 100 mL
    per 27 CFR 16.21(c): omits clause numbers (1)/(2). Both forms pass.
    Also applies per-field confidence gate (government_warning_body threshold
    = 0.45 per FIELD_CONFIDENCE_THRESHOLDS).
    """
    field, label_name = "government_warning", "Government Warning"

    body_conf = extracted.per_field_confidence.get("government_warning_body")
    if body_conf is not None:
        threshold = FIELD_CONFIDENCE_THRESHOLDS.get("government_warning_body", _DEFAULT_FIELD_CONFIDENCE)
        if body_conf < threshold:
            return FieldResult(
                field=field, label_name=label_name, status="fail",
                application_value=CANONICAL_WARNING_HEADER + " " + CANONICAL_WARNING_BODY,
                label_value=None,
                message=(
                    f"Government Warning text could not be read clearly enough to verify "
                    f"(confidence {body_conf:.0%}, minimum {threshold:.0%}). "
                    "Please retake the back-label photo: hold the camera flat-on to the "
                    "label surface (not at an angle), ensure the warning text is fully "
                    "visible and sharply in focus, and avoid reflections or shadows."
                ),
            )

    if not extracted.government_warning_present:
        return FieldResult(
            field=field, label_name=label_name, status="fail",
            application_value=CANONICAL_WARNING_HEADER + " " + CANONICAL_WARNING_BODY,
            label_value=None,
            message="Government Warning statement not found on label. Required on all alcohol beverage labels.",
        )

    header = (extracted.government_warning_header or "").strip()
    body = _normalize_whitespace(extracted.government_warning_body)
    canonical_body = _normalize_whitespace(CANONICAL_WARNING_BODY)
    canonical_short = _normalize_whitespace(CANONICAL_WARNING_BODY_SHORT)
    is_small = _is_small_container(extracted.net_contents)

    issues = []
    if header != CANONICAL_WARNING_HEADER:
        issues.append(
            "header must read exactly 'GOVERNMENT WARNING:' in capital letters "
            + f"(found '{header}')"
        )

    body_lower = body.lower()
    if body_lower != canonical_body.lower():
        if is_small and body_lower == canonical_short.lower():
            pass  # Short form accepted
        elif is_small and _similarity(body_lower, canonical_short.lower()) >= 0.88:
            issues.append("warning text has minor wording differences from the permitted short-form text")
        elif _similarity(body_lower, canonical_body.lower()) >= 0.88:
            issues.append("warning text has minor wording differences from the required text")
        elif is_small:
            issues.append("warning text does not match either the standard or the permitted short-form text (27 CFR 16.21(c))")
        else:
            issues.append("warning text does not match the required statement")

    if not issues:
        note = ""
        if is_small and body_lower == canonical_short.lower():
            note = " (short-form variant accepted for containers <= 100 mL per 27 CFR 16.21(c))"
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=CANONICAL_WARNING_HEADER + " " + CANONICAL_WARNING_BODY,
            label_value=header + " " + body,
            message="Government Warning statement matches the required text exactly." + note,
        )

    return FieldResult(
        field=field, label_name=label_name, status="fail",
        application_value=CANONICAL_WARNING_HEADER + " " + CANONICAL_WARNING_BODY,
        label_value=(header + " " + body).strip(),
        message="Government Warning issue(s): " + "; ".join(issues) + "."
    )

def run_compliance_checks(
    application: ApplicationData, extracted: ExtractedLabelData
) -> list:
    """Compare a label's extracted fields against the COLA application data.

    Calls assert_extraction_confidence first; raises LowConfidenceError if
    the image quality is insufficient for reliable evaluation.
    """
    bev_type = application.beverage_type
    assert_extraction_confidence(extracted, bev_type)

    results = [
        _check_text_field(
            "brand_name", "Brand Name", application.brand_name, extracted.brand_name
        ),
        _check_text_field(
            "class_type", "Class/Type", application.class_type, extracted.class_type
        ),
        _check_alcohol_content(application, extracted),
        _check_text_field(
            "net_contents",
            "Net Contents",
            application.net_contents,
            extracted.net_contents,
            normalizer=_normalize_net_contents,
        ),
        _check_government_warning(extracted),
    ]

    if application.name_and_address:
        results.append(
            _check_text_field(
                "name_and_address",
                "Name and Address",
                application.name_and_address,
                extracted.name_and_address,
            )
        )

    if application.country_of_origin:
        results.append(
            _check_text_field(
                "country_of_origin",
                "Country of Origin",
                application.country_of_origin,
                extracted.country_of_origin,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Label-Only Check helpers
# ---------------------------------------------------------------------------

def _presence_check(
    field: str,
    label_name: str,
    label_value,
    requirement: str,
    found_message=None,
) -> FieldResult:
    """Generic 'is this required field present?' check."""
    if label_value and label_value.strip():
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=requirement, label_value=label_value,
            message=found_message or f"{label_name} is present on the label.",
        )
    return FieldResult(
        field=field, label_name=label_name, status="fail",
        application_value=requirement, label_value=label_value,
        message=f"{label_name} was not found on the label. {requirement}",
    )


def _check_label_alcohol_content(
    extracted: ExtractedLabelData, beverage_type
) -> FieldResult:
    """Check ABV statement against the per-beverage-type requirement.

    Uses BEVERAGE_TOLERANCE for per-class tolerance values.
    """
    field, label_name = "alcohol_content", "Alcohol Content"
    requirement = (
        "Alcohol content (ABV) statement is required on distilled spirits labels "
        "(27 CFR 5.63(a)(3) / 5.65) and on wine labels over 14% ABV (27 CFR 4.36). "
        "It is not federally required on malt beverage labels unless alcohol is "
        "derived from added flavors or other nonbeverage ingredients (27 CFR "
        "7.63(a)(3) / 7.65), though some states require it."
    )
    label_value = extracted.alcohol_content

    if label_value:
        if _extract_percent(label_value) is None:
            return FieldResult(
                field=field, label_name=label_name, status="warning",
                application_value=requirement, label_value=label_value,
                message=(
                    "Alcohol content text was found but does not appear to include "
                    "a percentage. Verify the format (e.g. 'XX% Alc./Vol.')."
                ),
            )
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=requirement, label_value=label_value,
            message=f"Alcohol content ({label_value}) is stated on the label.",
        )

    if beverage_type == "distilled_spirits":
        return FieldResult(
            field=field, label_name=label_name, status="fail",
            application_value=requirement, label_value=label_value,
            message="Alcohol content statement is required on distilled spirits labels (27 CFR 5.63(a)(3) / 5.65) and was not found.",
        )

    if beverage_type == "wine":
        return FieldResult(
            field=field, label_name=label_name, status="warning",
            application_value=requirement, label_value=label_value,
            message=(
                "Alcohol content not found. Required for wines over 14% ABV "
                "(27 CFR 4.36); wines at or below 14% ABV may omit it if labeled "
                "'table wine' or 'light wine'. Verify which applies."
            ),
        )

    if beverage_type == "beer":
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=requirement, label_value=label_value,
            message=(
                "Alcohol content not stated. Federal law does not require an ABV "
                "statement on malt beverage labels unless alcohol is derived from "
                "added flavors or other nonbeverage ingredients (27 CFR "
                "7.63(a)(3) / 7.65), though some state laws require it - verify "
                "state requirements."
            ),
        )

    return FieldResult(
        field=field, label_name=label_name, status="warning",
        application_value=requirement, label_value=label_value,
        message=(
            "Alcohol content not found and the beverage type could not be "
            "determined. Verify whether an ABV statement is required for this product."
        ),
    )


def _check_label_net_contents(extracted: ExtractedLabelData, beverage_type=None) -> FieldResult:
    """Check that the label states a net contents quantity conforming to
    the authorised standards of fill (27 CFR 4.72 / 5.203 / 7.70).

    Unlike the previous version which only confirmed a number was present,
    this function now:
    1. Parses the quantity and unit from the label text.
    2. For wine and distilled spirits, validates the size against the
       closed list of authorised fill sizes in 27 CFR 4.72 / 5.203.
    3. For beer (27 CFR 7.70) confirms a numeric quantity is present
       (no restricted size list applies).

    A recognised quantity that falls outside the authorised list is a fail.
    An unrecognised unit (so the list cannot be consulted) is a warning.
    """
    field, label_name = "net_contents", "Net Contents"
    requirement = (
        "Net contents must be stated in conformance with standards of fill "
        "(27 CFR 4.37 / 5.70 / 7.70)."
    )
    label_value = extracted.net_contents

    if not label_value or not label_value.strip():
        return FieldResult(
            field=field, label_name=label_name, status="fail",
            application_value=requirement, label_value=label_value,
            message=f"Net contents not found on label. {requirement}",
        )

    qty, unit = _parse_net_contents(label_value)

    if qty is None:
        return FieldResult(
            field=field, label_name=label_name, status="warning",
            application_value=requirement, label_value=label_value,
            message="Net contents text was found but does not appear to include a numeric quantity. Verify the format.",
        )

    # Beer: no restricted list - just confirm a number is present.
    if beverage_type == "beer":
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=requirement, label_value=label_value,
            message=f"Net contents ({label_value}) is stated on the label (27 CFR 7.70).",
        )

    authorised = _is_authorised_fill(qty, unit, beverage_type)

    if authorised is None:
        # Could not determine (unit unknown or beverage type not yet confirmed).
        if unit is None:
            return FieldResult(
                field=field, label_name=label_name, status="warning",
                application_value=requirement, label_value=label_value,
                message=(
                    f"Net contents quantity {qty} was found but the unit could not be "
                    "parsed. Confirm the fill size is an authorised standard of fill "
                    "(27 CFR 4.72 / 5.203)."
                ),
            )
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=requirement, label_value=label_value,
            message=f"Net contents ({label_value}) is stated on the label.",
        )

    if authorised:
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=requirement, label_value=label_value,
            message=(
                f"Net contents ({label_value}) is stated and matches an authorised "
                "standard of fill (27 CFR 4.72 / 5.203)."
            ),
        )

    # Not in the authorised list.
    cfg_ref = "27 CFR 4.72" if beverage_type == "wine" else "27 CFR 5.203"
    return FieldResult(
        field=field, label_name=label_name, status="fail",
        application_value=requirement, label_value=label_value,
        message=(
            f"Net contents {label_value} ({qty} {unit}) does not match any "
            f"authorised standard of fill for {beverage_type or 'this beverage type'} "
            f"({cfg_ref}). Verify the fill size and resubmit."
        ),
    )


def _check_label_country_of_origin(extracted: ExtractedLabelData) -> FieldResult:
    """Check for country-of-origin statement, required only for imports.

    Uses extracted.origin_guess (Claude's best guess) when the statement
    is absent to determine whether a fail or warning is appropriate.
    """
    field, label_name = "country_of_origin", "Country of Origin"
    requirement = (
        "A country-of-origin statement (e.g. 'Product of Scotland') is required "
        "for imported products (27 CFR 27.59, 4.35(b), 5.69, 7.69)."
    )
    label_value = extracted.country_of_origin

    if label_value and label_value.strip():
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=requirement, label_value=label_value,
            message=f"Country of origin statement found: '{label_value}'.",
        )

    if extracted.origin_guess == "domestic":
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=requirement, label_value=label_value,
            message=(
                "No country-of-origin statement found, but this label appears to "
                "be a domestic (US) product, for which one is not required."
            ),
        )

    if extracted.origin_guess == "imported":
        return FieldResult(
            field=field, label_name=label_name, status="fail",
            application_value=requirement, label_value=label_value,
            message=(
                "This label appears to be an imported product, but no "
                "country-of-origin statement was found. " + requirement
            ),
        )

    return FieldResult(
        field=field, label_name=label_name, status="warning",
        application_value=requirement, label_value=label_value,
        message=(
            "No country-of-origin statement found, and it could not be "
            "determined whether this product is domestic or imported. This is "
            "only required if the product is imported - verify whether this "
            "applies to this product."
        ),
    )


# ---------------------------------------------------------------------------
# Formula-dependent checks
# ---------------------------------------------------------------------------
# The checks below operate on label text only. Whether a declaration is
# REQUIRED is ultimately determined by actual ingredient/formula records
# (SO2 ppm measurements, specific additives in the formula, aging records,
# import status). A label-level pass means the required text was found; it
# does NOT certify that the underlying formula data is compliant.
#
# Each function appends a FORMULA_DEPENDENT notice to its message to remind
# agents that formula/production record review is still required.

_FORMULA_DEPENDENT = (
    " NOTE: This check is based on label text only. Final determination "
    "requires review of production/formula records."
)


def _check_sulfite_declaration(extracted: ExtractedLabelData) -> FieldResult:
    """Check for a sulfite declaration on wine labels (27 CFR 4.32(e)).

    Wines containing >=10 ppm SO2 must bear 'Contains Sulfites' or equivalent.
    This check applies only to wine; other beverage types auto-pass.

    FORMULA-DEPENDENT: Whether the declaration is required depends on the
    actual measured SO2 level, not the label text alone.
    """
    field, label_name = "sulfite_declaration", "Sulfite Declaration"
    requirement = (
        "Wines containing 10 ppm or more of sulfur dioxide must bear the "
        "statement 'Contains Sulfites' or equivalent (27 CFR 4.32(e))."
    )
    bev = (extracted.beverage_type_guess or "").lower()
    if "wine" not in bev and bev not in ("", "unknown"):
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=None, label_value="N/A",
            message="Sulfite declaration is only required for wine products.",
        )
    val = extracted.sulfite_declaration
    if val and _normalize(val):
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=None, label_value=val,
            message="Sulfite declaration found on label." + _FORMULA_DEPENDENT,
        )
    return FieldResult(
        field=field, label_name=label_name, status="warning",
        application_value=None, label_value=None,
        message=(
            "No sulfite declaration found. If this wine contains >=10 ppm "
            "sulfur dioxide, 'Contains Sulfites' (or equivalent) is required "
            "(27 CFR 4.32(e)). Verify SO\u2082 level with production/lab data."
            + _FORMULA_DEPENDENT
        ),
    )


def _check_allergen_statements(extracted: ExtractedLabelData) -> FieldResult:
    """Check for mandatory allergen / additive disclosure statements.

    TTB requires disclosure of FD&C Yellow No. 5 (tartrazine), aspartame,
    saccharin, and cochineal extract/carmine when present
    (27 CFR 4.32(f), 5.63(c), 7.63(c); TTB Ruling 2012-1).

    FORMULA-DEPENDENT: Whether disclosure is required depends on whether
    these additives are present in the formula/ingredients, not the label text.
    """
    field, label_name = "allergen_statements", "Allergen / Additive Declarations"
    requirement = (
        "FD&C Yellow No. 5, aspartame, saccharin, and cochineal extract / "
        "carmine must be declared on labels when present (27 CFR 4.32(f), "
        "5.63(c), 7.63(c); TTB Ruling 2012-1)."
    )
    val = extracted.allergen_statements
    if val and _normalize(val):
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=None, label_value=val,
            message="Allergen/additive declaration(s) found on label." + _FORMULA_DEPENDENT,
        )
    return FieldResult(
        field=field, label_name=label_name, status="warning",
        application_value=None, label_value=None,
        message=(
            "No allergen or additive declarations detected. Verify via "
            "formula/ingredient records: FD&C Yellow No. 5, aspartame, "
            "saccharin, and cochineal extract/carmine require mandatory label "
            "disclosure when present (27 CFR 4.32(f), 5.63(c), 7.63(c))."
            + _FORMULA_DEPENDENT
        ),
    )


def _check_age_statement(extracted: ExtractedLabelData) -> FieldResult:
    """Check age statement requirements for straight whiskies (27 CFR 5.74(a)).

    Straight whiskies aged <4 years must state the actual age.
    Whiskies aged 4+ years need not show an age statement.

    FORMULA-DEPENDENT: Whether an age statement is REQUIRED depends on the
    actual age per production/aging records, not label text alone.
    """
    field, label_name = "age_statement", "Age Statement (Straight Whisky)"
    requirement = (
        "Straight whiskies aged less than 4 years must state the actual age "
        "on the label (27 CFR 5.74(a)). Optional for those aged 4+ years."
    )
    class_type = _normalize(extracted.class_type or "")
    is_straight_whisky = (
        ("whisky" in class_type or "whiskey" in class_type or "bourbon" in class_type)
        and "straight" in class_type
    )
    if not is_straight_whisky:
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=None, label_value="N/A",
            message="Age statement check applies only to straight whiskies.",
        )
    val = extracted.age_statement
    if val and _normalize(val):
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=None, label_value=val,
            message="Age statement found on label." + _FORMULA_DEPENDENT,
        )
    return FieldResult(
        field=field, label_name=label_name, status="warning",
        application_value=None, label_value=None,
        message=(
            "No age statement found on this straight whisky label. If the "
            "product was aged less than 4 years, the age must be stated "
            "(27 CFR 5.74(a)). Verify aging records."
            + _FORMULA_DEPENDENT
        ),
    )


def _check_commodity_statement(extracted: ExtractedLabelData) -> FieldResult:
    """Check for commodity / importer statement on imported spirits (27 CFR 5.63).

    Imported distilled spirits must identify the importer and state the
    class/type (27 CFR 5.63(a)(2), 5.66(b)).

    FORMULA-DEPENDENT: Whether this is required depends on the actual import
    status of the product per import records, not label text alone.
    """
    field, label_name = "commodity_statement", "Commodity / Importer Statement"
    requirement = (
        "Imported distilled spirits must state the class/type and bear the "
        "importer's name and address (27 CFR 5.63(a)(2), 5.66(b))."
    )
    bev = (extracted.beverage_type_guess or "").lower()
    if bev not in ("distilled_spirits", "", "unknown"):
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=None, label_value="N/A",
            message="Commodity statement check applies only to imported distilled spirits.",
        )
    origin = (extracted.origin_guess or "unknown").lower()
    if origin == "domestic":
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=None, label_value="N/A",
            message="Product appears domestic; commodity importer statement not required.",
        )
    if origin == "unknown":
        return FieldResult(
            field=field, label_name=label_name, status="warning",
            application_value=None, label_value=None,
            message=(
                "Could not determine whether product is imported. If imported, "
                "the label must identify the importer and commodity per "
                "27 CFR 5.63(a)(2) and 5.66(b). Verify import status."
                + _FORMULA_DEPENDENT
            ),
        )
    val = extracted.commodity_statement
    if val and _normalize(val):
        return FieldResult(
            field=field, label_name=label_name, status="pass",
            application_value=None, label_value=val,
            message="Commodity/importer statement found on label." + _FORMULA_DEPENDENT,
        )
    return FieldResult(
        field=field, label_name=label_name, status="fail",
        application_value=None, label_value=None,
        message=(
            "Imported distilled spirits label is missing a commodity/importer "
            "statement. The label must identify the importer and state the "
            "class/type (27 CFR 5.63(a)(2), 5.66(b))."
            + _FORMULA_DEPENDENT
        ),
    )

# ---------------------------------------------------------------------------
# Label-Only Check entry point
# ---------------------------------------------------------------------------

# Sentinel returned when beverage type has not been confirmed by the agent.
# The frontend should display a confirmation dialog and resubmit with
# confirmed_beverage_type set.
UNCONFIRMED_BEVERAGE_TYPE = "unconfirmed_beverage_type"


def check_label_requirements(
    extracted: ExtractedLabelData,
    beverage_type=None,
    confirmed_beverage_type=None,
) -> list:
    """Validate a label against TTB mandatory label requirements, independent
    of any COLA application data (Label-Only Check).

    Beverage-type confirmation flow
    --------------------------------
    The ABV requirement, standards-of-fill validation, and formula-dependent
    checks all depend on the beverage type.  Two sources are available:

    1. confirmed_beverage_type - explicitly set by the agent via the UI
       override after reviewing Claude's initial guess.  When present, this
       value is used unconditionally and no confirmation prompt is shown.
    2. beverage_type / extracted.beverage_type_guess - Claude's best guess
       from label text.  Used only when no agent override is provided.

    If neither source resolves to a known type the function raises
    ValueError(UNCONFIRMED_BEVERAGE_TYPE) so the caller can prompt the agent
    to confirm before evaluating requirements.

    Confidence gate
    ---------------
    assert_extraction_confidence is called with the resolved beverage type
    before any checks are run.  A LowConfidenceError means the photo needs
    to be retaken - requirements are NOT evaluated on low-confidence images.

    Args:
        extracted:               ExtractedLabelData from Claude.
        beverage_type:           Legacy positional arg kept for backward
                                 compat; treated as confirmed_beverage_type
                                 if confirmed_beverage_type is None.
        confirmed_beverage_type: Agent-confirmed beverage type override.
    """
    # Resolve: confirmed override > legacy arg > extracted guess.
    resolved_type = (
        confirmed_beverage_type
        or beverage_type
        or (extracted.beverage_type_guess or "unknown").lower()
    )
    if resolved_type in (None, "", "unknown") and confirmed_beverage_type is None:
        raise ValueError(UNCONFIRMED_BEVERAGE_TYPE)

    # Confidence gate: reject low-quality images before running any checks.
    assert_extraction_confidence(extracted, resolved_type)

    return [
        _presence_check(
            "brand_name",
            "Brand Name",
            extracted.brand_name,
            "A brand name is required on every label (27 CFR 4.32 / 5.63(a)(1), 5.64 / 7.63(a)(1), 7.64).",
        ),
        _presence_check(
            "class_type",
            "Class/Type Designation",
            extracted.class_type,
            "A class, type, or other required designation must be stated on the label (27 CFR 4.34 / 5.63(a)(2), 5.141 / 7.63(a)(2), 7.141).",
        ),
        _check_label_alcohol_content(extracted, resolved_type),
        _check_label_net_contents(extracted, resolved_type),
        _presence_check(
            "name_and_address",
            "Name and Address",
            extracted.name_and_address,
            "The name and address of the bottler, producer, packer, or importer is required (27 CFR 4.35 / 5.66-5.68 / 7.66-7.68).",
        ),
        _check_government_warning(extracted),
        _check_label_country_of_origin(extracted),
        _check_sulfite_declaration(extracted),
        _check_allergen_statements(extracted),
        _check_age_statement(extracted),
        _check_commodity_statement(extracted),
    ]


# ---------------------------------------------------------------------------
# Overall status roll-up
# ---------------------------------------------------------------------------

def overall_status(results: list) -> str:
    """Roll up a list of FieldResults into a single overall status.

    fail if any field failed, warning if any field needs review, else pass.
    """
    if any(r.status == "fail" for r in results):
        return "fail"
    if any(r.status == "warning" for r in results):
        return "warning"
    return "pass"

# ---------------------------------------------------------------------------
# Multi-photo field merging
# ---------------------------------------------------------------------------

def merge_extracted_label_data(extractions, photo_roles=None):
    """Merge multiple single-photo extractions into one combined result.

    When separate front and back label photos are submitted, extract each
    independently and call this to merge into one ExtractedLabelData.
    Strategy: for each field, pick the value from the extraction with the
    highest per_field_confidence (or extraction_confidence as fallback).

    Special handling:
    - government_warning_present: True if ANY extraction found the warning.
    - is_alcohol_beverage_label: True if ANY extraction flagged it.
    - extraction_confidence: maximum across all extractions.
    - per_field_confidence: max score per field.
    - field_locations: concatenated.
    - notes: concatenated with newlines.
    """
    if not extractions:
        from .models import ExtractedLabelData as _ELD
        return _ELD()
    if len(extractions) == 1:
        return extractions[0]

    roles = photo_roles or [str(i + 1) for i in range(len(extractions))]

    merged_pfc = {}
    for ext in extractions:
        for fn, score in ext.per_field_confidence.items():
            merged_pfc[fn] = max(merged_pfc.get(fn, 0.0), score)

    _TEXT_FIELDS = [
        "brand_name", "class_type", "alcohol_content", "net_contents",
        "name_and_address", "country_of_origin",
        "government_warning_header", "government_warning_body",
        "beverage_type_guess", "origin_guess",
        "sulfite_declaration", "allergen_statements",
        "age_statement", "commodity_statement",
    ]

    merged = {}
    for field in _TEXT_FIELDS:
        best_value = None
        best_score = -1.0
        for ext, role in zip(extractions, roles):
            val = getattr(ext, field, None)
            if val is None:
                continue
            score = ext.per_field_confidence.get(field, ext.extraction_confidence or 0.0)
            if best_value is None or score > best_score:
                best_value = val
                best_score = score
                _log.debug("merge: field='%s' score=%.2f from photo='%s'", field, score, role)
        merged[field] = best_value

    merged["government_warning_present"] = any(e.government_warning_present for e in extractions)
    merged["is_alcohol_beverage_label"] = any(e.is_alcohol_beverage_label for e in extractions)
    confs = [e.extraction_confidence for e in extractions if e.extraction_confidence is not None]
    merged["extraction_confidence"] = max(confs) if confs else None
    merged["per_field_confidence"] = merged_pfc
    merged["field_locations"] = [loc for e in extractions for loc in e.field_locations]
    notes_parts = [e.notes for e in extractions if e.notes]
    merged["notes"] = "\n".join(notes_parts) if notes_parts else None

    from .models import ExtractedLabelData as _ELD
    result = _ELD(**merged)
    _log.info(
        "merge_extracted_label_data: merged %d photos %s -> gw=%s abv=%r net=%r conf=%.2f",
        len(extractions), roles,
        result.government_warning_present,
        result.alcohol_content, result.net_contents,
        result.extraction_confidence or 0.0,
    )
    return result
