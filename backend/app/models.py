"""Pydantic models shared across the API: request/response bodies for
``main.py``, the data structures produced by ``claude_client.py``, and the
result types produced by ``compliance.py``.

These models are also the source of truth for the frontend's TypeScript
types in ``frontend/src/types.ts`` - keep the two in sync when changing
fields here.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

BeverageType = Literal["distilled_spirits", "wine", "beer"]
# "pass" = matches/compliant, "warning" = needs human review, "fail" = non-compliant.
Status = Literal["pass", "warning", "fail"]
# Claude's self-reported confidence in a single field's transcription.
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
    """Approximate location of an extracted field within one of the uploaded
    images, plus Claude's confidence in that reading. Coordinates are
    fractions (0-1) of the image's width/height, with (0, 0) at the top-left."""

    field: str
    image_index: int = 0
    confidence: Confidence = "medium"
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
    field_locations: list[FieldLocation] = Field(default_factory=list)
    notes: Optional[str] = None


class FieldResult(BaseModel):
    """Result of a single compliance/requirement check for one field.

    ``application_value`` holds the COLA application's value for
    ``run_compliance_checks`` results, or the relevant requirement/CFR
    citation text for ``check_label_requirements`` results.
    """

    field: str
    label_name: str
    status: Status
    application_value: Optional[str] = None
    label_value: Optional[str] = None
    message: str


class ReviewResult(BaseModel):
    """Response body for ``/api/review`` and ``/api/review/batch``: an
    application-vs-label comparison for one label."""

    filenames: list[str]
    overall_status: Status
    fields: list[FieldResult]
    extracted: ExtractedLabelData
    processing_time_ms: int
    error: Optional[str] = None


class LabelCheckResult(BaseModel):
    """Result of validating a label against TTB mandatory label requirements,
    independent of any COLA application data."""

    filenames: list[str]
    overall_status: Status
    beverage_type: Optional[str] = None
    checks: list[FieldResult]
    extracted: ExtractedLabelData
    processing_time_ms: int
    error: Optional[str] = None
