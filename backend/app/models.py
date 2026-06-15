"""Pydantic models shared across the API.

Changes in this version
------------------------
* ExtractedLabelData.extraction_confidence - overall readability float 0-1.
* ExtractedLabelData.per_field_confidence - per-field readability scores dict.
* LabelCheckResult.beverage_type_confirmed - bool.
* LabelCheckResult.needs_beverage_confirmation - bool.
* LabelCheckResult.photo_sources - list of photo roles used for merged extraction.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

BeverageType = Literal["distilled_spirits", "wine", "beer"]
Status = Literal["pass", "warning", "fail"]
Confidence = Literal["high", "medium", "low"]

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

class FieldResult(BaseModel):
    """Result of a single compliance/requirement check for one field."""

    field: str
    label_name: str
    status: Status
    application_value: Optional[str] = None
    label_value: Optional[str] = None
    message: str

class ReviewResult(BaseModel):
    """Response body for /api/review and /api/review/batch."""

    filenames: list[str]
    overall_status: Status
    fields: list[FieldResult]
    extracted: ExtractedLabelData
    processing_time_ms: int
    error: Optional[str] = None

class LabelCheckResult(BaseModel):
    """Result of validating a label against TTB mandatory label requirements.

    photo_sources lists the roles of photos merged to produce extracted,
    e.g. ["front", "back"] when separate label panel photos were submitted.
    """

    filenames: list[str]
    overall_status: Status
    fields: list[FieldResult]
    extracted: ExtractedLabelData
    processing_time_ms: int
    error: Optional[str] = None
    beverage_type_confirmed: bool = False
    needs_beverage_confirmation: bool = False
    photo_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Roles of photos merged to build extracted fields, e.g. "
            "['front', 'back']. Empty list when photo roles were not supplied."
        ),
    )
