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
from .errors import (
    PHOTO_QUALITY_LOW,
    PHOTO_UNREADABLE_FIELD,
    REQUIRED_ELEMENTS,
    RETAKE_MESSAGE,
    FieldPresence,
    ReadabilityAnalysis,
    RetakeCause,
    RetakeGuidance,
    UnreadableField,
    build_retake,
    infer_retake_cause,
)
from .models import (
    ApplicationData,
    ExtractedLabelData,
    FieldResult,
    TypeSizeMeasurement,
    WarningFormatFinding,
    WarningFormatResult,
    WarningTextDiff,
    WarningWordChange,
)


# ---------------------------------------------------------------------------
# Confidence gating
# ---------------------------------------------------------------------------

class LowConfidenceError(ValueError):
    """Raised when the whole image is too low-quality to evaluate.

    Per-field unreadability no longer raises: callers receive partial
    findings plus a retake object. Callers (main.py) must catch this and
    return HTTP 200 with overall_status=retake, never an unhandled 500.
    """

    def __init__(
        self,
        user_message: str,
        confidence: float,
        threshold: float,
        *,
        error_code: str = PHOTO_QUALITY_LOW,
        retake: RetakeGuidance | None = None,
        field_status: dict | None = None,
    ) -> None:
        self.user_message = user_message
        self.confidence = confidence
        self.threshold = threshold
        self.error_code = error_code
        self.retake = retake
        self.field_status = field_status or {}
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
CANONICAL_WARNING_FULL = CANONICAL_WARNING_HEADER + " " + CANONICAL_WARNING_BODY

# OCR punctuation that is equivalent to ASCII quotes / apostrophes (27 CFR 16.21
# comparison is otherwise exact at the word level).
_WARNING_QUOTE_TRANSLATION = str.maketrans({
    "\u2018": "'",
    "\u2019": "'",
    "\u201b": "'",
    "\u2032": "'",
    "\u0060": "'",
    "\u00b4": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201f": '"',
    "\u2033": '"',
})

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
# Standards of fill (27 CFR 4.72 / 5.203 / 7.70, incorporating T.D. TTB-200)
# ---------------------------------------------------------------------------
# All values in _WINE_FILL_ML and _SPIRITS_FILL_ML are in millilitres.
# Effective 2025-01-10 per Treasury Decision TTB-200 (89 FR 96570) and current eCFR.

# 27 CFR 4.72 - Wine (including T.D. TTB-200 authorized sizes)
_WINE_FILL_ML = frozenset({
    50, 100, 180, 187, 200, 250, 300, 330, 355, 360, 375, 473, 500, 550, 568,
    600, 620, 700, 720, 750, 1000, 1500, 1800, 2250, 3000,
})
_WINE_FILL_FLOZ = frozenset({
    1.7, 3.4, 6.1, 6.3, 6.8, 8.5, 10.1, 11.2, 12.0, 12.2, 12.7, 16.0, 16.9,
    18.6, 19.2, 20.3, 21.0, 23.7, 24.3, 25.4, 33.8, 50.7, 60.9, 76.1, 101.4,
})

# 27 CFR 5.203 - Distilled spirits (all containers, including T.D. TTB-200 authorized sizes)
_SPIRITS_FILL_ML = frozenset({
    50, 100, 187, 200, 250, 331, 350, 355, 375, 475, 500, 570, 700, 710, 720,
    750, 900, 945, 1000, 1500, 1750, 1800, 2000, 3000, 3750,
})
_SPIRITS_FILL_FLOZ = frozenset({
    1.7, 3.4, 6.3, 6.8, 8.5, 11.2, 11.8, 12.0, 12.7, 16.0, 16.1, 16.9, 19.3,
    23.7, 24.0, 24.3, 25.4, 30.4, 32.0, 33.8, 50.7, 59.2, 60.9, 67.6, 101.4, 126.8,
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


def _is_authorised_fill(qty: float, unit, beverage_type) -> bool:
    """Return True/False if qty/unit is an authorised standard of fill.

    Returns None if the check is not applicable or cannot be determined
    (e.g. beer, or unknown unit).

    Citations:
    - Wine: 27 CFR 4.72 and T.D. TTB-200 (effective 2025-01-10)
    - Distilled spirits: 27 CFR 5.203 and T.D. TTB-200 (effective 2025-01-10)
    - Malt beverages (beer): 27 CFR 7.70 (unrestricted fill sizes)

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
        if beverage_type == "wine":
            if any(abs(qty - a) <= _FILL_ML_TOLERANCE for a in _WINE_FILL_ML):
                return True
            # Wine > 4 L: must be in even whole liters per 27 CFR 4.72
            if qty >= 3999.0:
                nearest_whole_l = round(qty / 1000.0) * 1000.0
                if nearest_whole_l >= 4000.0 and abs(qty - nearest_whole_l) <= _FILL_ML_TOLERANCE:
                    return True
            return False
        if beverage_type == "distilled_spirits":
            return any(abs(qty - a) <= _FILL_ML_TOLERANCE for a in _SPIRITS_FILL_ML)
    if unit == "floz":
        qty_ml = qty * 29.573535
        if beverage_type == "wine":
            if any(abs(qty - a) <= _FILL_FLOZ_TOLERANCE for a in _WINE_FILL_FLOZ):
                return True
            if any(abs(qty_ml - a) <= _FILL_ML_TOLERANCE for a in _WINE_FILL_ML):
                return True
            if qty_ml >= 3999.0:
                nearest_whole_l = round(qty_ml / 1000.0) * 1000.0
                if nearest_whole_l >= 4000.0 and abs(qty_ml - nearest_whole_l) <= _FILL_ML_TOLERANCE:
                    return True
            return False
        if beverage_type == "distilled_spirits":
            if any(abs(qty - a) <= _FILL_FLOZ_TOLERANCE for a in _SPIRITS_FILL_FLOZ):
                return True
            return any(abs(qty_ml - a) <= _FILL_ML_TOLERANCE for a in _SPIRITS_FILL_ML)
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


def _normalize_warning_ocr(text) -> str:
    """Normalize OCR noise that 27 CFR 16.21 comparison must tolerate.

    Tolerated: whitespace runs, line breaks (including hyphenated wraps),
    and curly versus straight quotes or apostrophes. Word identity is preserved.
    """
    if not text:
        return ""
    text = str(text).translate(_WARNING_QUOTE_TRANSLATION)
    text = re.sub(r"-\s*[\r\n]+\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize_warning_words(text) -> list[str]:
    """Split warning text into words after OCR-tolerant whitespace/quote normalize."""
    normalized = _normalize_warning_ocr(text)
    return normalized.split() if normalized else []


def _combined_warning_text(header, body) -> str:
    """Join extracted header and body, avoiding a duplicated header prefix."""
    header = (header or "").strip()
    body = (body or "").strip()
    if not header:
        return body
    if not body:
        return header
    if _normalize_warning_ocr(body).lower().startswith(_normalize_warning_ocr(header).lower()):
        return body
    return f"{header} {body}"


def _government_warning_header_is_all_caps(header: str, combined: str) -> bool:
    """Return True when the first two 16.21 words are GOVERNMENT WARNING in all caps.

    Prefer the dedicated header field so a duplicated title-case prefix in the
    body cannot discard a valid all-caps heading.
    """
    source = header.strip() if header and str(header).strip() else combined
    words = tokenize_warning_words(source)
    if len(words) < 2:
        return False
    second = words[1][:-1] if words[1].endswith(":") else words[1]
    return words[0] == "GOVERNMENT" and second == "WARNING"


def diff_warning_words(expected: str, observed: str) -> WarningTextDiff:
    """Word-level diff of observed warning text against the exact 16.21 statement.

    Comparison is case-insensitive so an all-caps body still matches. Dropped,
    added, or substituted words are never tolerated. Positions are 0-based word
    indexes in the respective sequences.
    """
    expected_words = tokenize_warning_words(expected)
    observed_words = tokenize_warning_words(observed)
    matcher = SequenceMatcher(
        a=[w.lower() for w in expected_words],
        b=[w.lower() for w in observed_words],
        autojunk=False,
    )
    missing: list[WarningWordChange] = []
    extra: list[WarningWordChange] = []
    changed: list[WarningWordChange] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            for idx in range(i1, i2):
                missing.append(
                    WarningWordChange(
                        kind="missing",
                        expected=expected_words[idx],
                        expected_index=idx,
                    )
                )
        elif tag == "insert":
            for idx in range(j1, j2):
                extra.append(
                    WarningWordChange(
                        kind="extra",
                        observed=observed_words[idx],
                        observed_index=idx,
                    )
                )
        elif tag == "replace":
            paired = min(i2 - i1, j2 - j1)
            for offset in range(paired):
                changed.append(
                    WarningWordChange(
                        kind="changed",
                        expected=expected_words[i1 + offset],
                        observed=observed_words[j1 + offset],
                        expected_index=i1 + offset,
                        observed_index=j1 + offset,
                    )
                )
            for idx in range(i1 + paired, i2):
                missing.append(
                    WarningWordChange(
                        kind="missing",
                        expected=expected_words[idx],
                        expected_index=idx,
                    )
                )
            for idx in range(j1 + paired, j2):
                extra.append(
                    WarningWordChange(
                        kind="extra",
                        observed=observed_words[idx],
                        observed_index=idx,
                    )
                )

    return WarningTextDiff(
        exact_match=not (missing or extra or changed),
        expected_words=expected_words,
        observed_words=observed_words,
        missing_words=missing,
        extra_words=extra,
        changed_words=changed,
    )


def _type_size_tier(net_contents) -> tuple[float, int]:
    """Return (min height mm, max characters per inch) for the 16.22 container tier."""
    from .cv_typesize import get_max_characters_per_inch, get_required_min_height_mm

    return get_required_min_height_mm(net_contents), get_max_characters_per_inch(net_contents)


def _build_warning_format(
    extracted: ExtractedLabelData,
    type_size_details: Optional[TypeSizeMeasurement],
) -> WarningFormatResult:
    """Roll type-size, characters-per-inch, and bold-header checks into one result."""
    required_min_mm, max_cpi = _type_size_tier(extracted.net_contents)
    estimated_cpi = None
    if type_size_details is not None:
        if type_size_details.required_min_height_mm:
            required_min_mm = type_size_details.required_min_height_mm
        if type_size_details.max_characters_per_inch:
            max_cpi = type_size_details.max_characters_per_inch
        estimated_cpi = type_size_details.estimated_characters_per_inch

    findings: list[WarningFormatFinding] = []

    if type_size_details is not None:
        if type_size_details.state == "fail":
            findings.append(
                WarningFormatFinding(
                    code="type_size",
                    status="fail",
                    citation="27 CFR 16.22(b)",
                    message=type_size_details.verification_note,
                    estimated=False,
                )
            )
        elif type_size_details.state in ("warning", "cannot_measure"):
            findings.append(
                WarningFormatFinding(
                    code="type_size",
                    status="advisory",
                    citation="27 CFR 16.22(b)",
                    message=type_size_details.verification_note,
                    estimated=type_size_details.state == "warning",
                )
            )
        elif type_size_details.state == "pass":
            findings.append(
                WarningFormatFinding(
                    code="type_size",
                    status="pass",
                    citation="27 CFR 16.22(b)",
                    message=type_size_details.verification_note,
                    estimated=False,
                )
            )

    if estimated_cpi is not None:
        if estimated_cpi > max_cpi:
            cpi_message = (
                f"Estimated {estimated_cpi:.1f} characters per inch exceeds the "
                f"27 CFR 16.22(a)(4) maximum of {max_cpi} for this container's "
                f"{required_min_mm:.0f} mm type-size tier. This measurement is an "
                "estimate from the photo and is advisory only."
            )
        else:
            cpi_message = (
                f"Estimated {estimated_cpi:.1f} characters per inch is within the "
                f"27 CFR 16.22(a)(4) maximum of {max_cpi} for this container's "
                f"{required_min_mm:.0f} mm type-size tier. This measurement is an "
                "estimate from the photo and is advisory only."
            )
        findings.append(
            WarningFormatFinding(
                code="characters_per_inch",
                status="advisory",
                citation="27 CFR 16.22(a)(4)",
                message=cpi_message,
                estimated=True,
            )
        )

    if extracted.government_warning_header_appears_bold is False:
        findings.append(
            WarningFormatFinding(
                code="header_bold",
                status="fail",
                citation="27 CFR 16.22(a)(2)",
                message=(
                    "The 'GOVERNMENT WARNING' heading does not appear to be in bold type "
                    "(27 CFR 16.22(a)(2))."
                ),
                estimated=True,
            )
        )

    if any(f.status == "fail" for f in findings):
        format_status = "fail"
    elif any(f.status == "advisory" for f in findings):
        format_status = "advisory"
    else:
        format_status = "pass"

    return WarningFormatResult(
        status=format_status,
        required_min_height_mm=required_min_mm,
        max_characters_per_inch=max_cpi,
        estimated_characters_per_inch=estimated_cpi,
        findings=findings,
    )


def _describe_word_diff(diff: WarningTextDiff) -> str:
    """Plain-language summary of missing, extra, and changed 16.21 words."""
    parts: list[str] = []
    if diff.missing_words:
        words = ", ".join(
            f"'{item.expected}' (pos {item.expected_index})" for item in diff.missing_words
        )
        parts.append(f"missing {words}")
    if diff.extra_words:
        words = ", ".join(
            f"'{item.observed}' (pos {item.observed_index})" for item in diff.extra_words
        )
        parts.append(f"extra {words}")
    if diff.changed_words:
        words = ", ".join(
            f"'{item.expected}'→'{item.observed}' (expected pos {item.expected_index}, "
            f"observed pos {item.observed_index})"
            for item in diff.changed_words
        )
        parts.append(f"changed {words}")
    return "; ".join(parts)


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


def _alcohol_is_proof_only(text) -> bool:
    """True when alcohol is stated in proof without a percent ABV."""
    if not text or not str(text).strip():
        return False
    if _extract_percent(text) is not None:
        return False
    return bool(re.search(r"\bproof\b", str(text), re.I))


def warning_looks_like_ocr_noise(diff: WarningTextDiff) -> bool:
    """True when a 16.21 mismatch looks like a misread, not a wording change.

    Real defects (added/dropped words of three or more letters, or a
    substitution that is not a close edit) fail immediately. One-or-two
    character substitutions on otherwise aligned words are treated as
    borderline OCR so a second look can run before we fail the label.
    """
    if diff.exact_match:
        return False
    long_tokens = [
        (item.expected or item.observed or "")
        for item in list(diff.missing_words) + list(diff.extra_words)
        if len((item.expected or item.observed or "")) >= 3
    ]
    if long_tokens:
        return False
    if not diff.changed_words:
        return False
    for item in diff.changed_words:
        expected = (item.expected or "").lower()
        observed = (item.observed or "").lower()
        if not expected or not observed:
            return False
        if abs(len(expected) - len(observed)) > 2:
            return False
        if _similarity(expected, observed) < 0.75:
            return False
    return True


def warning_needs_second_look(diff: WarningTextDiff, observed: str) -> bool:
    """True when observed warning text is close enough to retry before failing."""
    if diff.exact_match or not warning_looks_like_ocr_noise(diff):
        return False
    ratio = _similarity(
        _normalize_warning_ocr(CANONICAL_WARNING_FULL),
        _normalize_warning_ocr(observed),
    )
    return 0.88 <= ratio < 1.0


# ---------------------------------------------------------------------------
# Confidence-gate helper
# ---------------------------------------------------------------------------

def _canonical_field_name(field_name: str) -> str:
    """Map header/body warning keys onto the single required-element name."""
    if field_name in {"government_warning_header", "government_warning_body"}:
        return "government_warning"
    return field_name


def _field_value(extracted: ExtractedLabelData, field_name: str):
    if field_name == "government_warning":
        header = (extracted.government_warning_header or "").strip()
        body = (extracted.government_warning_body or "").strip()
        return header or body or None
    return getattr(extracted, field_name, None)


def _has_value(value) -> bool:
    return bool(value and str(value).strip() and _normalize(value))


def _warning_is_absent(extracted: ExtractedLabelData) -> bool:
    """True only when the warning is confidently not on the label.

    A present=True signal with empty transcribed text is unreadable, not
    absent — the model thought a warning was there but could not read it.
    """
    if extracted.government_warning_present:
        return False
    header = (extracted.government_warning_header or "").strip()
    body = (extracted.government_warning_body or "").strip()
    return not header and not body


def _confidence_threshold(
    extracted: ExtractedLabelData, beverage_type: str | None = None
) -> float:
    """Same class threshold used by assert_extraction_confidence."""
    cls = str(beverage_type or extracted.beverage_type_guess or "unknown").lower()
    return BEVERAGE_TOLERANCE.get(cls, BEVERAGE_TOLERANCE["unknown"]).get(
        "min_confidence", _DEFAULT_MIN_CONFIDENCE
    )


def _is_optional_zero_score(
    field_name: str,
    field_score: float,
    extracted: ExtractedLabelData,
    beverage_type: str | None = None,
) -> bool:
    """True when a 0.0 score means 'not on this label', not 'could not read'.

    The extraction prompt scores an absent printed field as 0.0 with an empty
    value. On a readable photo (overall confidence at or above the class
    threshold) that combination is a compliance miss — 'X not found on
    label' — not a retake. A low-but-nonzero score, or empty text on an
    unreadable photo, stays unreadable.
    """
    if field_name == "country_of_origin" and field_score == 0.0:
        return True
    if field_name in {"government_warning_header", "government_warning_body"}:
        if _warning_is_absent(extracted):
            return True
        # present=True with empty text is unreadable, not a confident absence.
        if not _has_value(_field_value(extracted, "government_warning")):
            return False
    if field_score != 0.0:
        return False
    canonical = _canonical_field_name(field_name)
    value = _field_value(extracted, canonical)
    if _has_value(value):
        return False
    overall = extracted.extraction_confidence
    if overall is None:
        return True
    return overall >= _confidence_threshold(extracted, beverage_type)


def collect_unreadable_fields(
    extracted: ExtractedLabelData,
    quality_cause: RetakeCause | None = None,
    beverage_type: str | None = None,
) -> list[UnreadableField]:
    """Return per-field confidence misses without raising.

    Absent government-warning text and missing country-of-origin on domestic
    labels are skipped so they stay compliance failures, not retakes.
    When the pre-gate already named a cause, that value fills the retake
    object instead of inferring from extraction notes.
    """
    if not extracted.per_field_confidence:
        return []

    cause = quality_cause or infer_retake_cause(getattr(extracted, "notes", None))
    issues: list[UnreadableField] = []
    seen: set[str] = set()
    for field_name, field_score in extracted.per_field_confidence.items():
        if _is_optional_zero_score(
            field_name, field_score, extracted, beverage_type=beverage_type
        ):
            continue
        field_threshold = FIELD_CONFIDENCE_THRESHOLDS.get(field_name, _DEFAULT_FIELD_CONFIDENCE)
        if field_score >= field_threshold:
            continue
        canonical = _canonical_field_name(field_name)
        if canonical in seen:
            continue
        seen.add(canonical)
        _log.info(
            "Low per-field confidence for '%s': %.2f < %.2f (unreadable, not absent)",
            canonical,
            field_score,
            field_threshold,
        )
        issues.append(
            UnreadableField(
                field=canonical,
                score=field_score,
                threshold=field_threshold,
                area=build_retake(canonical, cause).area,
                cause=cause,
                tip=build_retake(canonical, cause).tip,
            )
        )
    return issues


def analyze_readability(
    extracted: ExtractedLabelData,
    quality_cause: RetakeCause | None = None,
    beverage_type: str | None = None,
) -> ReadabilityAnalysis:
    """Build field_status and retake guidance from extraction confidence.

    Unreadable is distinct from absent: a low-confidence empty field is
    unreadable and must not be reported as a missing-element violation.
    """
    unreadable = collect_unreadable_fields(
        extracted, quality_cause=quality_cause, beverage_type=beverage_type
    )
    unreadable_keys = {item.field for item in unreadable}
    field_status: dict[str, FieldPresence] = {}

    extra_keys = {
        _canonical_field_name(name)
        for name in (extracted.per_field_confidence or {})
        if _canonical_field_name(name) not in REQUIRED_ELEMENTS
        and not _is_optional_zero_score(
            name, extracted.per_field_confidence[name], extracted, beverage_type=beverage_type
        )
    }
    for field_name in list(REQUIRED_ELEMENTS) + sorted(extra_keys):
        if field_name in unreadable_keys:
            field_status[field_name] = "unreadable"
            continue
        if field_name == "government_warning":
            field_status[field_name] = "absent" if _warning_is_absent(extracted) else "present"
            continue
        value = _field_value(extracted, field_name)
        field_status[field_name] = "present" if _has_value(value) else "absent"

    retake = None
    error_code = None
    if unreadable:
        first = min(unreadable, key=lambda item: item.score)
        retake = build_retake(first.field, first.cause, area=first.area, tip=first.tip)
        error_code = PHOTO_UNREADABLE_FIELD
    return ReadabilityAnalysis(
        field_status=field_status,
        unreadable=unreadable,
        retake=retake,
        error_code=error_code,
    )


def unreadable_field_names(
    extracted: ExtractedLabelData,
    quality_cause: RetakeCause | None = None,
    beverage_type: str | None = None,
) -> set[str]:
    """Canonical field names that must be skipped by compliance checks."""
    return {
        item.field
        for item in collect_unreadable_fields(
            extracted, quality_cause=quality_cause, beverage_type=beverage_type
        )
    }


def assert_extraction_confidence(extracted, beverage_type=None, quality_cause=None):
    """Raise LowConfidenceError only when the whole image is unreadable.

    Per-field confidence misses are collected by ``analyze_readability`` so
    readable findings can still be returned.  A LowConfidenceError here means
    overall extraction_confidence is below the per-class threshold.
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
        cause = quality_cause or infer_retake_cause(getattr(extracted, "notes", None))
        analysis = analyze_readability(
            extracted, quality_cause=quality_cause, beverage_type=beverage_type
        )
        overall_unreadable = {name: "unreadable" for name in REQUIRED_ELEMENTS}
        overall_unreadable.update(analysis.field_status)
        for name in REQUIRED_ELEMENTS:
            overall_unreadable[name] = "unreadable"
        raise LowConfidenceError(
            user_message=RETAKE_MESSAGE,
            confidence=conf,
            threshold=threshold,
            error_code=PHOTO_QUALITY_LOW,
            retake=build_retake("label", cause),
            field_status=overall_unreadable,
        )

# ---------------------------------------------------------------------------
# Brand Name vs. Producer Address Containment Guard (Issue #9 P1)
# ---------------------------------------------------------------------------

_PRODUCER_PREFIXES: tuple[str, ...] = (
    "distilled and bottled by",
    "distilled & bottled by",
    "distilled and packaged by",
    "distilled & packaged by",
    "distilled and canned by",
    "distilled & canned by",
    "distilled by",
    "produced and bottled by",
    "produced & bottled by",
    "produced and packaged by",
    "produced & packaged by",
    "produced and cellared by",
    "produced & cellared by",
    "produced by",
    "bottled by",
    "brewed and bottled by",
    "brewed & bottled by",
    "brewed and canned by",
    "brewed & canned by",
    "brewed and packaged by",
    "brewed & packaged by",
    "brewed by",
    "vinted and bottled by",
    "vinted & bottled by",
    "vinted and cellared by",
    "vinted & cellared by",
    "vinted and packaged by",
    "vinted & packaged by",
    "vinted by",
    "cellared and bottled by",
    "cellared & bottled by",
    "cellared by",
    "imported and bottled by",
    "imported & bottled by",
    "imported by",
    "packed by",
    "packed for",
    "blended and bottled by",
    "blended & bottled by",
    "blended by",
    "made by",
    "crafted by",
    "canned by",
)


# Legal-entity suffixes only. Industry words (winery, distillery, brewing)
# stay part of the company name so a shorter display brand is not treated
# as "the bottler name" when bounding boxes are missing.
_LEGAL_ENTITY_SUFFIXES = frozenset({
    "co", "llc", "inc", "ltd", "corp", "corporation", "company", "lp", "plc",
})

# First token of a wrapped line that continues the company name rather than
# starting the city/street (extraction keeps printed line breaks).
_COMPANY_CONTINUATION_WORDS = frozenset({
    "winery", "wineries", "distillery", "distilleries", "distilling",
    "brewing", "brewery", "breweries", "cellars", "cellar", "vineyard",
    "vineyards", "company", "co", "llc", "inc", "ltd", "corp", "corporation",
    "partners", "imports", "importing", "trading", "group", "farms",
    "orchards", "estate", "estates",
})

_US_STATE_ABBREVS = frozenset({
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
})

_US_STATE_NAMES = frozenset({
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "newhampshire", "newjersey", "newmexico", "newyork", "northcarolina",
    "northdakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhodeisland", "southcarolina", "southdakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "westvirginia",
    "wisconsin", "wyoming", "districtofcolumbia",
})


def _token_spans(text: str) -> list[tuple[str, int, int]]:
    """Return (normalized_word, start, end) for each non-whitespace token."""
    spans: list[tuple[str, int, int]] = []
    for match in re.finditer(r"\S+", text):
        norm = re.sub(r"[^a-z0-9&]+", "", match.group().lower())
        if norm:
            spans.append((norm, match.start(), match.end()))
    return spans


def _strip_producer_prefix_raw(name_and_address: str) -> str:
    """Remove a leading bottled-by / produced-by clause from a raw address line.

    Prefix words match across any whitespace, including printed line breaks,
    so ``Produced and\\nBottled by …`` strips the same as a single-line clause.
    The remainder keeps its original spelling and punctuation.
    """
    text = name_and_address.strip()
    spans = _token_spans(text)
    for prefix in _PRODUCER_PREFIXES:
        prefix_words = [re.sub(r"[^a-z0-9&]+", "", word) for word in prefix.split()]
        prefix_words = [word for word in prefix_words if word]
        count = len(prefix_words)
        if count == 0 or len(spans) < count:
            continue
        if [spans[i][0] for i in range(count)] == prefix_words:
            return text[spans[count - 1][2]:].strip().strip(",;:-")
    return text


def _first_line_token(line: str) -> str:
    match = re.search(r"[A-Za-z0-9]+", line)
    return match.group().lower() if match else ""


def _is_zip_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d{5}(?:\d{4})?", token))


def _strip_trailing_zip_tokens(tokens: list[str]) -> list[str]:
    """Drop trailing USPS ZIP tokens, including a hyphen-split ZIP+4 (5 + 4)."""
    work = list(tokens)
    if (
        len(work) >= 2
        and re.fullmatch(r"\d{5}", work[-2])
        and re.fullmatch(r"\d{4}", work[-1])
    ):
        work = work[:-2]
    while work and _is_zip_token(work[-1]):
        work = work[:-1]
    return work


def _tokens_are_state(tokens: list[str]) -> bool:
    if not tokens:
        return False
    if len(tokens) == 1 and len(tokens[0]) == 2 and tokens[0].lower() in _US_STATE_ABBREVS:
        return True
    return "".join(token.lower() for token in tokens) in _US_STATE_NAMES


def _clause_is_state_or_zip(clause: str) -> bool:
    tokens = re.findall(r"[A-Za-z0-9]+", clause)
    if not tokens:
        return False
    rest = _strip_trailing_zip_tokens(tokens)
    if not rest:
        return True
    if rest != tokens and all(token.isdigit() for token in rest):
        return True
    return _tokens_are_state(rest)


def _is_city_state_or_street_line(line: str) -> bool:
    """True for a city/state/ZIP or street line, not a wrapped company suffix."""
    tokens = re.findall(r"[A-Za-z0-9]+", line)
    if not tokens:
        return False
    if tokens[0].isdigit():
        return True
    clauses = [part.strip() for part in re.split(r"[,;]", line) if part.strip()]
    if len(clauses) >= 2:
        return any(_clause_is_state_or_zip(part) for part in clauses[1:])
    work = _strip_trailing_zip_tokens(tokens)
    peeled_zip = work != tokens
    if len(work) >= 2 and _tokens_are_state(work[-1:]):
        # "Hopline Brewing Co" is a company; "Denver CO 80202" is Colorado.
        if work[-1].lower() in _LEGAL_ENTITY_SUFFIXES and not peeled_zip:
            return False
        return True
    if len(work) >= 2:
        for start in range(1, len(work)):
            if _tokens_are_state(work[start:]):
                return True
    return False


def _company_clause_from_remainder(remainder: str) -> str:
    """Take the company name, joining wraps and stopping at the address."""
    parts: list[str] = []
    seen_company = False
    for raw_chunk in re.split(r"[\r\n]+", remainder):
        chunk = raw_chunk.strip()
        if not chunk:
            continue
        first = _first_line_token(chunk)
        if seen_company and first not in _COMPANY_CONTINUATION_WORDS:
            if not first or _is_city_state_or_street_line(chunk):
                break
        clause = re.split(r"[,;]", chunk, maxsplit=1)[0].strip()
        if clause:
            parts.append(clause)
            seen_company = True
        if "," in chunk or ";" in chunk:
            break
    return " ".join(parts)


def _producer_entity_name(name_and_address: str | None) -> str:
    """Return the normalized bottler/producer company name, without city/state.

    Uses the clause after a standard producer prefix and before the city or
    street. Printed wraps inside the company name (``Crescent Ridge\\nWinery``)
    stay part of the entity; a following city/state line does not.
    Does not require field_locations.
    """
    if not name_and_address or not str(name_and_address).strip():
        return ""
    remainder = _strip_producer_prefix_raw(str(name_and_address))
    if not remainder:
        return ""
    return _normalize(_company_clause_from_remainder(remainder))


def _drop_legal_suffixes(normalized: str) -> str:
    words = normalized.split()
    while words and words[-1] in _LEGAL_ENTITY_SUFFIXES:
        words.pop()
    return " ".join(words)


def _brand_text_is_derived_from_bottler(
    brand_name: str | None, name_and_address: str | None
) -> bool:
    """True when brand text is the bottler/producer company name itself.

    A shorter display brand that only shares words with the company
    (Crescent Ridge vs Crescent Ridge Winery) is not derived. Used when
    brand and address bounding boxes are missing.
    """
    brand = _normalize(brand_name)
    if not brand or not name_and_address:
        return False

    company = _producer_entity_name(name_and_address)
    if not company:
        return False

    stripped = _normalize(_strip_producer_prefix_raw(str(name_and_address)))
    if brand == company or (stripped and brand == stripped):
        return True
    if company in brand:
        return True
    if brand in company:
        leftover = [w for w in company.replace(brand, " ", 1).split() if w]
        if leftover and all(w in _LEGAL_ENTITY_SUFFIXES for w in leftover):
            return True

    brand_core = _drop_legal_suffixes(brand)
    company_core = _drop_legal_suffixes(company)
    return bool(brand_core and brand_core == company_core)


def _is_brand_in_address(brand_name: str | None, name_and_address: str | None) -> bool:
    """Return True if brand_name is equal, near-equal, or a substring of name_and_address.

    Advisory overlap detector used when bounding boxes are present. This
    check must not blank the extracted brand or fail a label solely because
    the display brand also appears in the bottler/producer line.
    """
    if not brand_name or not name_and_address:
        return False

    norm_brand = _normalize(brand_name)
    norm_addr = _normalize(name_and_address)

    if not norm_brand or not norm_addr:
        return False

    # Direct substring containment
    if norm_brand in norm_addr:
        return True

    # Strip standard bottler/producer prefixes from address
    stripped_addr = norm_addr
    for prefix in _PRODUCER_PREFIXES:
        norm_prefix = _normalize(prefix)
        if stripped_addr.startswith(norm_prefix):
            stripped_addr = stripped_addr[len(norm_prefix):].strip()
            break

    if norm_brand in stripped_addr:
        return True

    # Reverse containment: if stripped producer entity is in norm_brand
    if stripped_addr and stripped_addr in norm_brand:
        return True

    # Similarity check against stripped producer entity or whole address
    if _similarity(norm_brand, stripped_addr) >= 0.75 or _similarity(norm_brand, norm_addr) >= 0.75:
        return True

    # Significant word containment (words of len >= 3 excluding stop words)
    stop_words = {"the", "and", "co", "llc", "inc", "ltd", "corp", "corporation", "by", "cellars", "distillery", "distilling", "winery", "brewing", "brewery", "company"}
    brand_words = [w for w in norm_brand.split() if len(w) >= 3 and w not in stop_words]
    if brand_words and all(w in norm_addr for w in brand_words):
        return True

    return False


def _spatial_brand_vs_address(extracted: ExtractedLabelData) -> bool | None:
    """Compare brand_name and name_and_address boxes when both are present.

    Returns True when the boxes are on different panels or are vertically
    (>15%) or horizontally (>20%) separated, False when both boxes exist
    but overlap, and None when either box is missing. Missing boxes must
    not be treated as a failed distinct-brand check.
    """
    if not extracted.field_locations:
        return None
    brand_locs = [loc for loc in extracted.field_locations if getattr(loc, "field", None) == "brand_name"]
    addr_locs = [loc for loc in extracted.field_locations if getattr(loc, "field", None) == "name_and_address"]
    if not brand_locs or not addr_locs:
        return None
    for b_loc in brand_locs:
        for a_loc in addr_locs:
            if getattr(b_loc, "image_index", 0) != getattr(a_loc, "image_index", 0):
                return True
            if abs(b_loc.y - a_loc.y) > 0.15:
                return True
            if abs(b_loc.x - a_loc.x) > 0.20:
                return True
    return False


def _has_distinct_brand_evidence(extracted: ExtractedLabelData) -> bool:
    """True when notes or spatially separated boxes show a distinct brand heading.

    Bounding boxes refine the decision only when both locations are present.
    Missing boxes are not evidence against a distinct brand.
    """
    if extracted.notes and "distinct brand" in extracted.notes.lower():
        return True
    return _spatial_brand_vs_address(extracted) is True


def _should_warn_brand_derived_from_bottler(
    extracted: ExtractedLabelData,
    application_address: str | None = None,
) -> bool:
    """Whether to emit the brand-derived-from-bottler advisory.

    When brand and address boxes are present they refine the decision:
    separated boxes pass; overlapping boxes still use the address-overlap
    detector. When boxes are missing, warn only if the brand text really
    is the bottler/producer company name (match or company-name substring),
    not merely a display brand that shares words with the producer.
    """
    if _has_distinct_brand_evidence(extracted):
        return False

    spatial = _spatial_brand_vs_address(extracted)
    if spatial is True:
        return False
    if spatial is False:
        return (
            _is_brand_in_address(extracted.brand_name, extracted.name_and_address)
            or _is_brand_in_address(extracted.brand_name, application_address)
        )

    return (
        _brand_text_is_derived_from_bottler(extracted.brand_name, extracted.name_and_address)
        or _brand_text_is_derived_from_bottler(extracted.brand_name, application_address)
    )


def _check_text_field(
    field: str,
    label_name: str,
    application_value: str,
    label_value,
    normalizer=_normalize,
) -> FieldResult:
    """Compare an application value to the corresponding label value.

    - Missing/empty on label or application -> fail.
    - Equal after normalisation -> pass.
    - >=85% similar after normalisation -> warning (cosmetic difference).
    - Otherwise -> fail.
    """
    norm_app = normalizer(application_value)
    norm_label = normalizer(label_value)

    if not label_value or not str(label_value).strip() or not norm_label:
        return FieldResult(
            field=field, label_name=label_name, status="fail",
            application_value=application_value, label_value=label_value,
            message=f"{label_name} not found on label.",
        )

    if not application_value or not str(application_value).strip() or not norm_app:
        return FieldResult(
            field=field, label_name=label_name, status="fail",
            application_value=application_value, label_value=label_value,
            message=f"{label_name} missing from application data.",
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


def _check_brand_name_match(
    application: ApplicationData, extracted: ExtractedLabelData
) -> FieldResult:
    """Compare application brand name to label brand name with an advisory guard.

    Warn only when the extracted brand is the bottler/producer company name
    (or, when boxes exist and overlap, when the brand was read from the
    address block). A distinct display brand that shares words with the
    producer does not warn when boxes are missing. Do not fail or blank
    brand solely because the display name also appears in the bottler line.
    """
    base_result = _check_text_field(
        "brand_name", "Brand Name", application.brand_name, extracted.brand_name
    )
    if base_result.status != "pass":
        return base_result

    if _should_warn_brand_derived_from_bottler(extracted, application.name_and_address):
        return FieldResult(
            field="brand_name",
            label_name="Brand Name",
            status="warning",
            application_value=application.brand_name,
            label_value=extracted.brand_name,
            message=(
                f"Advisory: Brand name '{extracted.brand_name}' appears to be contained within or derived from "
                "the bottler/producer name and address block without distinct brand heading evidence on the label. "
                "Requires human review to confirm whether a distinct brand name appears on the label (27 CFR 4.32 / 5.64 / 7.64)."
            ),
        )

    return base_result


def _check_label_brand_name(extracted: ExtractedLabelData) -> FieldResult:
    """Validate mandatory brand name requirement on label (Label-Only Check).

    Warn only when brand text is the bottler/producer company name, or when
    overlapping boxes show the brand was read from the address block. Do not
    fail or blank brand solely because the display name also appears in the
    bottler/producer line.
    """
    req = "A brand name is required on every label (27 CFR 4.32 / 5.63(a)(1), 5.64 / 7.63(a)(1), 7.64)."
    if not extracted.brand_name or not str(extracted.brand_name).strip() or not _normalize(extracted.brand_name):
        return FieldResult(
            field="brand_name",
            label_name="Brand Name",
            status="fail",
            application_value=req,
            label_value=extracted.brand_name,
            message=f"Brand Name was not found on the label. {req}",
        )

    if _should_warn_brand_derived_from_bottler(extracted):
        return FieldResult(
            field="brand_name",
            label_name="Brand Name",
            status="warning",
            application_value=req,
            label_value=extracted.brand_name,
            message=(
                f"Advisory: Brand name '{extracted.brand_name}' appears to be contained within or derived from "
                "the bottler/producer name and address line without distinct brand heading evidence. "
                "Requires human review to confirm whether a distinct brand name is present (27 CFR 4.32 / 5.64 / 7.64)."
            ),
        )

    return FieldResult(
        field="brand_name",
        label_name="Brand Name",
        status="pass",
        application_value=req,
        label_value=extracted.brand_name,
        message="Brand Name is present on the label.",
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

    if _alcohol_is_proof_only(label_value):
        return FieldResult(
            field=field, label_name=label_name, status="fail",
            application_value=application.alcohol_content, label_value=label_value,
            message=(
                "Alcohol content is stated in proof only. The alcohol content "
                "must be stated as a percentage of alcohol by volume "
                "(27 CFR 4.36 / 5.65 / 7.65)."
            ),
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


def _check_government_warning(
    extracted: ExtractedLabelData,
    type_size_details: Optional[TypeSizeMeasurement] = None,
    second_look=None,
) -> FieldResult:
    """Validate the Government Warning statement (27 CFR 16.21 / 16.22).

    Every container size is checked against the full statutory 16.21 text.
    Part 16 does not permit a shortened warning. Small containers receive only
    the smaller 16.22 type-size minimum (and matching characters-per-inch cap).

    Text comparison is a local word-level diff. OCR whitespace, line breaks,
    and curly versus straight quotes are tolerated; dropped, added, or
    substituted words are not. GOVERNMENT WARNING must be all capitals.

    16.22 type size and heading boldness are rolled into ``warning_format``.
    A heading that does not appear bold is a hard fail (27 CFR 16.22(a)(2)).
    Estimated characters-per-inch remains advisory.
    """
    field, label_name = "government_warning", "Government Warning"
    warning_format = _build_warning_format(extracted, type_size_details)

    # Issue #5 W02: If government warning is absent (present=False or empty), emit status=fail directly
    # without triggering low-confidence photo retake or error.
    if not extracted.government_warning_present or (
        not (extracted.government_warning_header or "").strip()
        and not (extracted.government_warning_body or "").strip()
    ):
        return FieldResult(
            field=field, label_name=label_name, status="fail",
            application_value=CANONICAL_WARNING_FULL,
            label_value=None,
            message="Government Warning statement not found on label. Required on all alcohol beverage labels (27 CFR Part 16).",
            type_size_details=type_size_details,
            warning_format=warning_format,
        )

    body_conf = extracted.per_field_confidence.get("government_warning_body")
    if body_conf is not None:
        threshold = FIELD_CONFIDENCE_THRESHOLDS.get("government_warning_body", _DEFAULT_FIELD_CONFIDENCE)
        if body_conf < threshold:
            return FieldResult(
                field=field, label_name=label_name, status="fail",
                application_value=CANONICAL_WARNING_FULL,
                label_value=None,
                message=(
                    f"Government Warning text could not be read clearly enough to verify "
                    f"(confidence {body_conf:.0%}, minimum {threshold:.0%}). "
                    "Please retake the back-label photo: hold the camera flat-on to the "
                    "label surface (not at an angle), ensure the warning text is fully "
                    "visible and sharply in focus, and avoid reflections or shadows."
                ),
                type_size_details=type_size_details,
                warning_format=warning_format,
            )

    header = (extracted.government_warning_header or "").strip()
    observed = _combined_warning_text(header, extracted.government_warning_body)
    label_value = _normalize_warning_ocr(observed)
    text_diff = diff_warning_words(CANONICAL_WARNING_FULL, observed)
    header_caps_ok = _government_warning_header_is_all_caps(header, observed)
    normalized_header = _normalize_warning_ocr(header) if header else ""

    header_is_exact = header_caps_ok and (
        not normalized_header or normalized_header == CANONICAL_WARNING_HEADER
    )
    if (
        header_is_exact
        and not text_diff.exact_match
        and warning_needs_second_look(text_diff, observed)
    ):
        confirmed = None
        if callable(second_look):
            try:
                confirmed = second_look(observed)
            except Exception as exc:  # noqa: BLE001
                _log.debug("Government Warning second look failed: %s", exc)
                confirmed = None
        if confirmed:
            retry_diff = diff_warning_words(CANONICAL_WARNING_FULL, confirmed)
            if retry_diff.exact_match:
                text_diff = retry_diff
                observed = confirmed
                label_value = _normalize_warning_ocr(confirmed)
        else:
            return FieldResult(
                field=field, label_name=label_name, status="warning",
                application_value=CANONICAL_WARNING_FULL,
                label_value=label_value,
                message=(
                    "Government Warning text is close to the required 27 CFR 16.21 "
                    "statement but could not be confirmed exactly. Please retake the "
                    "back-label photo so the warning can be read without glare or blur."
                ),
                type_size_details=type_size_details,
                warning_format=warning_format,
                warning_text_diff=text_diff,
            )

    issues: list[str] = []
    if not header_caps_ok or (normalized_header and normalized_header != CANONICAL_WARNING_HEADER):
        issues.append(
            f"header must appear in capital letters exactly as '{CANONICAL_WARNING_HEADER}' "
            f"(found '{header or ' '.join(tokenize_warning_words(observed)[:2])}') (27 CFR 16.21/16.22)"
        )
    if not text_diff.exact_match:
        issues.append(
            "warning text does not match the required 27 CFR 16.21 statement ("
            + _describe_word_diff(text_diff)
            + ")"
        )
    if any(f.code == "header_bold" and f.status == "fail" for f in warning_format.findings):
        issues.append("heading must appear in bold type (27 CFR 16.22(a)(2))")

    text_ok = not issues
    cpi_over_limit = (
        warning_format.estimated_characters_per_inch is not None
        and warning_format.estimated_characters_per_inch > warning_format.max_characters_per_inch
    )
    advisory_only_format = any(
        f.code == "header_bold" and f.status == "advisory"
        for f in warning_format.findings
    ) or cpi_over_limit

    if text_ok:
        if type_size_details is None:
            message = (
                "Government Warning statement matches the required text exactly. "
                "(Note: Physical type size in mm per 27 CFR 16.22 is not verified from "
                "photos and requires physical gauge measurement.)"
            )
            status = "warning" if advisory_only_format else "pass"
        elif type_size_details.state == "fail":
            message = (
                "Government Warning statement text is valid, but type size is deficient: "
                + type_size_details.verification_note
            )
            status = "fail"
        elif type_size_details.state == "cannot_measure":
            message = (
                "Government Warning text matches required wording. Calibrated measurement "
                "could not be completed: " + type_size_details.verification_note
            )
            status = "warning"
        elif type_size_details.state == "pass":
            message = (
                "Government Warning statement matches the required text exactly and meets "
                "statutory type-size requirements. (" + type_size_details.verification_note + ")"
            )
            status = "warning" if advisory_only_format else "pass"
        else:
            message = (
                "Government Warning statement matches the required text exactly. ("
                + type_size_details.verification_note + ")"
            )
            status = "warning" if advisory_only_format else "pass"

        if advisory_only_format and status != "fail":
            advisory_notes = [
                f.message for f in warning_format.findings
                if f.code in ("header_bold", "characters_per_inch") and f.status == "advisory"
            ]
            if advisory_notes:
                message = message + " Format advisory: " + " ".join(advisory_notes)

        return FieldResult(
            field=field, label_name=label_name, status=status,
            application_value=CANONICAL_WARNING_FULL,
            label_value=label_value,
            message=message,
            type_size_details=type_size_details,
            warning_text_diff=text_diff,
            warning_format=warning_format,
        )

    return FieldResult(
        field=field, label_name=label_name, status="fail",
        application_value=CANONICAL_WARNING_FULL,
        label_value=label_value,
        message="Government Warning issue(s): " + "; ".join(issues) + ".",
        type_size_details=type_size_details,
        warning_text_diff=text_diff,
        warning_format=warning_format,
    )

def run_compliance_checks(
    application: ApplicationData,
    extracted: ExtractedLabelData,
    type_size_details: Optional[TypeSizeMeasurement] = None,
    quality_cause: Optional[RetakeCause] = None,
) -> list[FieldResult]:
    """Compare a label's extracted fields against the COLA application data.

    Calls assert_extraction_confidence first; raises LowConfidenceError if
    the image quality is insufficient for reliable evaluation.
    """
    bev_type = application.beverage_type
    assert_extraction_confidence(extracted, bev_type, quality_cause=quality_cause)
    skip = unreadable_field_names(
        extracted, quality_cause=quality_cause, beverage_type=bev_type
    )

    results = []
    if "brand_name" not in skip:
        results.append(_check_brand_name_match(application, extracted))
    if "class_type" not in skip:
        results.append(
            _check_text_field(
                "class_type", "Class/Type", application.class_type, extracted.class_type
            )
        )
    if "alcohol_content" not in skip:
        results.append(_check_alcohol_content(application, extracted))
    if "net_contents" not in skip:
        results.append(_check_net_contents_review(application, extracted))
    if "government_warning" not in skip:
        results.append(_check_government_warning(extracted, type_size_details=type_size_details))
    if application.beverage_type == "wine" and "sulfite_declaration" not in skip:
        sulfite_src = extracted
        if "wine" not in (extracted.beverage_type_guess or "").lower():
            sulfite_src = extracted.model_copy(update={"beverage_type_guess": "wine"})
        results.append(_check_sulfite_declaration(sulfite_src))

    if application.name_and_address and "name_and_address" not in skip:
        results.append(
            _check_text_field(
                "name_and_address",
                "Name and Address",
                application.name_and_address,
                extracted.name_and_address,
            )
        )

    if application.country_of_origin and "country_of_origin" not in skip:
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
    if label_value and str(label_value).strip() and _normalize(label_value):
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


def _check_net_contents_review(
    application: ApplicationData, extracted: ExtractedLabelData
) -> FieldResult:
    """Match net contents to the application, then apply standards of fill."""
    match = _check_text_field(
        "net_contents",
        "Net Contents",
        application.net_contents,
        extracted.net_contents,
        normalizer=_normalize_net_contents,
    )
    if not extracted.net_contents or not str(extracted.net_contents).strip():
        return match
    sof = _check_label_net_contents(extracted, application.beverage_type)
    if sof.status == "fail":
        return FieldResult(
            field="net_contents",
            label_name="Net Contents",
            status="fail",
            application_value=application.net_contents,
            label_value=extracted.net_contents,
            message=sof.message,
        )
    return match


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
        if _alcohol_is_proof_only(label_value):
            return FieldResult(
                field=field, label_name=label_name, status="fail",
                application_value=requirement, label_value=label_value,
                message=(
                    "Alcohol content is stated in proof only. The alcohol content "
                    "must be stated as a percentage of alcohol by volume "
                    "(27 CFR 4.36 / 5.65 / 7.65)."
                ),
            )
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
    the authorised standards of fill (27 CFR 4.72 / 5.203 / 7.70 and T.D. TTB-200).

    Unlike the previous version which only confirmed a number was present,
    this function now:
    1. Parses the quantity and unit from the label text.
    2. For wine and distilled spirits, validates the size against the
       closed list of authorised fill sizes in 27 CFR 4.72 / 5.203 / T.D. TTB-200.
    3. For beer (27 CFR 7.70) confirms a numeric quantity is present
       (no restricted size list applies).

    A recognised quantity that falls outside the authorised list is a fail.
    An unrecognised unit (so the list cannot be consulted) is a warning.
    """
    field, label_name = "net_contents", "Net Contents"
    requirement = (
        "Net contents must be stated in conformance with standards of fill "
        "(27 CFR 4.37 / 5.70 / 7.70 and T.D. TTB-200)."
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
                    "(27 CFR 4.72 / 5.203 / T.D. TTB-200)."
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
                "standard of fill (27 CFR 4.72 / 5.203 / T.D. TTB-200)."
            ),
        )

    # Not in the authorised list.
    cfg_ref = "27 CFR 4.72 / T.D. TTB-200" if beverage_type == "wine" else "27 CFR 5.203 / T.D. TTB-200"
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
        field=field, label_name=label_name, status="pass",
        application_value=None, label_value=None,
        message=(
            "No allergen or additive declarations detected. This is informational "
            "only and is not a label defect: FD&C Yellow No. 5, aspartame, "
            "saccharin, and cochineal extract/carmine require disclosure only "
            "when present in the formula (27 CFR 4.32(f), 5.63(c), 7.63(c))."
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
    type_size_details: Optional[TypeSizeMeasurement] = None,
    quality_cause: Optional[RetakeCause] = None,
) -> list[FieldResult]:
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

    # Confidence gate: reject wholly unreadable images before running checks.
    # Per-field unreadability is skipped so it is never reported as absent.
    assert_extraction_confidence(extracted, resolved_type, quality_cause=quality_cause)
    skip = unreadable_field_names(
        extracted, quality_cause=quality_cause, beverage_type=resolved_type
    )

    checks: list[FieldResult] = []
    if "brand_name" not in skip:
        checks.append(_check_label_brand_name(extracted))
    if "class_type" not in skip:
        checks.append(
            _presence_check(
                "class_type",
                "Class/Type Designation",
                extracted.class_type,
                "A class, type, or other required designation must be stated on the label (27 CFR 4.34 / 5.63(a)(2), 5.141 / 7.63(a)(2), 7.141).",
            )
        )
    if "alcohol_content" not in skip:
        checks.append(_check_label_alcohol_content(extracted, resolved_type))
    if "net_contents" not in skip:
        checks.append(_check_label_net_contents(extracted, resolved_type))
    if "name_and_address" not in skip:
        checks.append(
            _presence_check(
                "name_and_address",
                "Name and Address",
                extracted.name_and_address,
                "The name and address of the bottler, producer, packer, or importer is required (27 CFR 4.35 / 5.66-5.68 / 7.66-7.68).",
            )
        )
    if "government_warning" not in skip:
        checks.append(_check_government_warning(extracted, type_size_details=type_size_details))
    if "country_of_origin" not in skip:
        checks.append(_check_label_country_of_origin(extracted))
    if "sulfite_declaration" not in skip:
        checks.append(_check_sulfite_declaration(extracted))
    if "allergen_statements" not in skip:
        checks.append(_check_allergen_statements(extracted))
    if "age_statement" not in skip:
        checks.append(_check_age_statement(extracted))
    if "commodity_statement" not in skip:
        checks.append(_check_commodity_statement(extracted))
    return checks


# ---------------------------------------------------------------------------
# Overall status roll-up
# ---------------------------------------------------------------------------

def overall_status(results: list) -> str:
    """Roll up a list of FieldResults (or status strings) into a single overall status.

    fail if any field failed, warning if any field needs review, else pass.
    """
    statuses = [r.status if hasattr(r, "status") else r for r in results]
    if any(s == "fail" for s in statuses):
        return "fail"
    if any(s == "warning" for s in statuses):
        return "warning"
    return "pass"

# ---------------------------------------------------------------------------
# Multi-photo field merging
# ---------------------------------------------------------------------------

def merge_extracted_label_data(extractions, photo_roles=None):
    """Merge multiple single-photo extractions into one combined result.

    When separate front and back label photos are submitted, extract each
    independently and call this to merge into one ExtractedLabelData.
    Strategy: for each field, pick non-empty values over empty/whitespace values,
    breaking ties using the highest per_field_confidence (or extraction_confidence
    as fallback). This ensures statements present on any panel (such as sulfite
    declarations or government warnings on a back label) win even when another
    panel has higher overall image confidence but empty text for that field.

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
        best_is_nonempty = False
        for ext, role in zip(extractions, roles):
            val = getattr(ext, field, None)
            if val is None:
                continue
            is_nonempty = bool(str(val).strip())
            score = ext.per_field_confidence.get(field, ext.extraction_confidence or 0.0)
            if best_value is None:
                best_value = val
                best_score = score
                best_is_nonempty = is_nonempty
            elif is_nonempty and not best_is_nonempty:
                best_value = val
                best_score = score
                best_is_nonempty = True
                _log.debug("merge: field='%s' (non-empty win) score=%.2f from photo='%s'", field, score, role)
            elif (is_nonempty == best_is_nonempty) and score > best_score:
                best_value = val
                best_score = score
                _log.debug("merge: field='%s' score=%.2f from photo='%s'", field, score, role)
        merged[field] = best_value

    merged["government_warning_present"] = any(e.government_warning_present for e in extractions)
    bold_vals = [
        getattr(e, "government_warning_header_appears_bold", None)
        for e in extractions
        if e.government_warning_present
        or (e.government_warning_header or "").strip()
        or (e.government_warning_body or "").strip()
    ]
    if any(v is True for v in bold_vals):
        merged["government_warning_header_appears_bold"] = True
    elif any(v is False for v in bold_vals):
        merged["government_warning_header_appears_bold"] = False
    else:
        merged["government_warning_header_appears_bold"] = None
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
