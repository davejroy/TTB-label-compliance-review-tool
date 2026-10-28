"""Pydantic models shared across the API.

Changes in this version
------------------------
* ExtractedLabelData.extraction_confidence - overall readability float 0-1.
* ExtractedLabelData.per_field_confidence - per-field readability scores dict.
* LabelCheckResult.beverage_type_confirmed - bool.
* LabelCheckResult.needs_beverage_confirmation - bool.
* LabelCheckResult.photo_sources - list of photo roles used for merged extraction.
"""

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .errors import FieldPresence, RetakeGuidance


class ImageQualityAdvisory(BaseModel):
    """Warn-first photo-quality metrics attached to a review result.

    Present when the pre-gate ran and found something worth reporting.
    Does not change ``overall_status`` unless the gate short-circuited.
    """

    blur_score: float = Field(description="Laplacian variance (higher is sharper).")
    exposure: Literal["dark", "ok", "bright"] = Field(
        description="Mean-luma band: dark, ok, or bright."
    )
    glare_fraction: float = Field(description="Fraction of near-white (saturated) pixels.")
    resolution: dict[str, int] = Field(
        description="Original EXIF-corrected pixel size {width, height}."
    )
    warnings: list[str] = Field(default_factory=list, description="Human-readable quality notes.")

BeverageType = Literal["distilled_spirits", "wine", "beer"]
Status = Literal["pass", "warning", "fail"]
OverallStatus = Literal["pass", "warning", "fail", "retake", "error"]
Confidence = Literal["high", "medium", "low"]

class CalibrationMethod(str, Enum):
    SCALE_MARKER_ARUCO = "scale_marker_aruco"
    SCALE_MARKER_CARD_ID1 = "scale_marker_card_id1"
    SCALE_MARKER_RULER = "scale_marker_ruler"
    CONTAINER_GEOMETRY = "container_geometry"
    UNCALIBRATED_ESTIMATE = "uncalibrated_estimate"
    NONE = "none"

class TypeSizeMeasurement(BaseModel):
    """Calibrated physical type-size measurement result under 27 CFR 16.22."""

    method: CalibrationMethod = Field(
        default=CalibrationMethod.NONE,
        description="Calibration technique used: scale_marker_aruco, scale_marker_card_id1, scale_marker_ruler, container_geometry, uncalibrated_estimate, none.",
    )
    pixels_per_mm: Optional[float] = Field(None, description="Calibrated spatial resolution in px/mm")
    measured_capital_height_mm: Optional[float] = Field(None, description="Extracted capital letter height in mm (None if uncalibrated)")
    required_min_height_mm: float = Field(..., description="Statutory 16.22 threshold based on container capacity (>= 1.0 mm for <= 237 mL, >= 2.0 mm for 237 mL-3 L, >= 3.0 mm for > 3 L)")
    uncertainty_mm: Optional[float] = Field(None, description="95% confidence interval half-width (+/- mm)")
    skew_angle_deg: Optional[float] = Field(None, description="Planar tilt angle of target in degrees")
    state: Literal["pass", "fail", "warning", "cannot_measure"] = Field(
        ...,
        description="Type-size compliance state: pass | fail | warning | cannot_measure",
    )
    verification_note: str = Field(..., description="Plain-language audit explanation or advisory note")
    estimated_characters_per_inch: Optional[float] = Field(
        None,
        description="Estimated 27 CFR 16.22(a)(4) characters per inch from the photo (None when inches cannot be measured).",
    )
    max_characters_per_inch: Optional[int] = Field(
        None,
        description="Statutory 16.22(a)(4) maximum characters per inch for the container's type-size tier (40 / 25 / 12).",
    )

class ApplicationData(BaseModel):
    """Data entered by the agent from the COLA application form."""

    beverage_type: BeverageType
    brand_name: str
    class_type: str
    alcohol_content: str = Field(
        description="e.g. '45% Alc./Vol.' or '45'. Some wine/beer labels are exempt."
    )
    net_contents: str
    name_and_address: Optional[str] = None
    country_of_origin: Optional[str] = None

class FieldLocation(BaseModel):
    """Approximate bounding box for a field on the label image."""

    field: Optional[str] = None
    image_index: Optional[int] = 0
    confidence: Optional[Confidence] = None
    x: float
    y: float
    width: float
    height: float

class ExtractedLabelData(BaseModel):
    """Fields extracted from the label image by Claude's vision model."""

    brand_name: Optional[str] = None
    class_type: Optional[str] = None
    alcohol_content: Optional[str] = None
    net_contents: Optional[str] = None
    name_and_address: Optional[str] = None
    country_of_origin: Optional[str] = None
    government_warning_header: Optional[str] = None
    government_warning_body: Optional[str] = None
    government_warning_present: bool = False
    government_warning_header_appears_bold: Optional[bool] = Field(
        default=None,
        description=(
            "Vision estimate of whether the printed GOVERNMENT WARNING heading appears bold "
            "(27 CFR 16.22(a)(2)). None when unknown. False is a hard fail."
        ),
    )
    beverage_type_guess: Optional[str] = None
    origin_guess: Optional[Literal["domestic", "imported", "unknown"]] = None
    field_locations: list[FieldLocation] = Field(default_factory=list)
    notes: Optional[str] = None
    sulfite_declaration: Optional[str] = None
    allergen_statements: Optional[str] = None
    age_statement: Optional[str] = None
    commodity_statement: Optional[str] = None
    is_alcohol_beverage_label: bool = True
    extraction_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Overall readability score 0-1 assigned by Claude. "
            "Below the per-class threshold in BEVERAGE_TOLERANCE a "
            "LowConfidenceError is raised and a new photo is requested."
        ),
    )
    per_field_confidence: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Per-field readability scores 0-1 from Claude. When a field score "
            "is below its threshold in FIELD_CONFIDENCE_THRESHOLDS the "
            "compliance check fails and requests a targeted retake."
        ),
    )

class WarningWordChange(BaseModel):
    """One word-level difference against the exact 27 CFR 16.21 statement."""

    kind: Literal["missing", "extra", "changed"]
    expected: Optional[str] = None
    observed: Optional[str] = None
    expected_index: Optional[int] = Field(
        default=None,
        description="0-based word index in the statutory 16.21 word sequence.",
    )
    observed_index: Optional[int] = Field(
        default=None,
        description="0-based word index in the observed label word sequence.",
    )


class WarningTextDiff(BaseModel):
    """Word-level comparison of observed warning text against 27 CFR 16.21."""

    exact_match: bool
    expected_words: list[str] = Field(default_factory=list)
    observed_words: list[str] = Field(default_factory=list)
    missing_words: list[WarningWordChange] = Field(default_factory=list)
    extra_words: list[WarningWordChange] = Field(default_factory=list)
    changed_words: list[WarningWordChange] = Field(default_factory=list)


class WarningFormatFinding(BaseModel):
    """One 27 CFR 16.22 format check (type size, characters per inch, or bold header)."""

    code: Literal["type_size", "characters_per_inch", "header_bold"]
    status: Literal["pass", "advisory", "fail"]
    citation: str
    message: str
    estimated: bool = False


class WarningFormatResult(BaseModel):
    """Rolled-up 27 CFR 16.22 warning-format result for UI display."""

    status: Literal["pass", "advisory", "fail"]
    required_min_height_mm: float
    max_characters_per_inch: int
    estimated_characters_per_inch: Optional[float] = None
    findings: list[WarningFormatFinding] = Field(default_factory=list)


class FieldResult(BaseModel):
    """Result of a single compliance/requirement check for one field."""

    field: str
    label_name: str
    status: Status
    application_value: Optional[str] = None
    label_value: Optional[str] = None
    message: str
    type_size_details: Optional[TypeSizeMeasurement] = Field(
        default=None,
        description="27 CFR 16.22 physical type-size measurement details (Option A scale marker or uncalibrated advisory).",
    )
    warning_text_diff: Optional[WarningTextDiff] = Field(
        default=None,
        description="Word-level 27 CFR 16.21 diff (missing, extra, and changed words with positions).",
    )
    warning_format: Optional[WarningFormatResult] = Field(
        default=None,
        description="Rolled-up 27 CFR 16.22 type-size / characters-per-inch / bold-header result.",
    )

class StageTimings(BaseModel):
    """Per-stage durations in milliseconds for one label review.

    Keys match the intake/model/compliance pipeline. Values are milliseconds
    spent in that stage (summed across images for CPU stages). Cache token
    counts and TTFT come from the Anthropic usage object on each panel call.
    """

    decode: int = 0
    pre_gate: int = 0
    resize: int = 0
    model: int = 0
    compliance: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    model_ttft_ms: int = 0
    model_generation_ms: int = 0


class ReviewResult(BaseModel):
    """Response body for /api/review and /api/review/batch."""

    filenames: list[str]
    overall_status: OverallStatus
    fields: list[FieldResult]
    extracted: ExtractedLabelData
    processing_time_ms: int
    error: Optional[str] = None
    error_code: Optional[str] = Field(
        default=None,
        description="Stable machine-readable code when overall_status is retake or error.",
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Per-request correlation id; also sent as X-Request-ID.",
    )
    retake: Optional[RetakeGuidance] = Field(
        default=None,
        description="Recapture guidance when photo quality prevented a complete review.",
    )
    field_status: dict[str, FieldPresence] = Field(
        default_factory=dict,
        description="present / absent / unreadable for each required label element.",
    )
    quality: Optional[ImageQualityAdvisory] = Field(
        default=None,
        description="Pre-gate quality advisory (moderate issues; never blocks in warn mode).",
    )
    timings_ms: Optional[StageTimings] = Field(
        default=None,
        description=(
            "Per-stage milliseconds for this review (decode, pre_gate, resize, "
            "model, compliance). Additive; omitted only when timing was not collected."
        ),
    )

class LabelCheckResult(BaseModel):
    """Result of validating a label against TTB mandatory label requirements.

    photo_sources lists the roles of photos merged to produce extracted,
    e.g. ["front", "back"] when separate label panel photos were submitted.
    """

    filenames: list[str]
    overall_status: OverallStatus
    checks: list[FieldResult]
    extracted: ExtractedLabelData
    processing_time_ms: int
    error: Optional[str] = None
    error_code: Optional[str] = Field(
        default=None,
        description="Stable machine-readable code when overall_status is retake or error.",
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Per-request correlation id; also sent as X-Request-ID.",
    )
    retake: Optional[RetakeGuidance] = Field(
        default=None,
        description="Recapture guidance when photo quality prevented a complete review.",
    )
    field_status: dict[str, FieldPresence] = Field(
        default_factory=dict,
        description="present / absent / unreadable for each required label element.",
    )
    beverage_type: Optional[str] = None
    beverage_type_confirmed: bool = False
    needs_beverage_confirmation: bool = False
    photo_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Roles of photos merged to build extracted fields, e.g. "
            "['front', 'back']. Empty list when photo roles were not supplied."
        ),
    )
    quality: Optional[ImageQualityAdvisory] = Field(
        default=None,
        description="Pre-gate quality advisory (moderate issues; never blocks in warn mode).",
    )
    timings_ms: Optional[StageTimings] = Field(
        default=None,
        description=(
            "Per-stage milliseconds for this review (decode, pre_gate, resize, "
            "model, compliance). Additive; omitted only when timing was not collected."
        ),
    )

