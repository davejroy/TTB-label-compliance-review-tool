"""Pydantic models shared across the API: request/response bodies for
``main.py``, the data structures produced by ``claude_client.py``, and the
result types produced by ``compliance.py``.

These models are also the source of truth for the frontend's TypeScript
types in ``frontend/src/types.ts`` - keep the two in sync when changing
fields here.

Changes in this version
------------------------
* ``ExtractedLabelData.extraction_confidence`` - new float 0-1 field that
  Claude populates to signal overall label-image readability.  Used by
  ``compliance.assert_extraction_confidence`` to gate low-quality photos.
* ``LabelCheckResult.beverage_type_confirmed`` - bool flag indicating whether
  the beverage type was explicitly confirmed by the agent (True) or is still
  Claude's best guess (False).
* ``LabelCheckResult.needs_beverage_confirmation`` - bool flag; True when the
  label-only check could not resolve a beverage type and is waiting for the
  agent to confirm before requirements are evaluated.
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
    # Overall extraction confidence: 0.0 (no readable text) to 1.0 (crystal clear).
    # Set by Claude based on image readability; used by assert_extraction_confidence()
    # in compliance.py to gate low-quality photos before any check is run.
    extraction_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Overall readability score 0.0-1.0 assigned by Claude. "
            "Below the per-class threshold in BEVERAGE_TOLERANCE a "
            "LowConfidenceError is raised and a new photo is requested."
        ),
    )


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
    """Response body for ``/api/review`` and ``/api/review/batch``."""

    filenames: list[str]
    overall_status: Status
    fields: list[FieldResult]
    extracted: ExtractedLabelData
    processing_time_ms: int
    error: Optional[str] = None


class LabelCheckResult(BaseModel):
    """Result of validating a label against TTB mandatory label requirements,
    independent of any COLA application data (Label-Only Check).

    New fields
    ----------
    beverage_type_confirmed : bool
        True if the agent explicitly confirmed/overrode the beverage type.
        False if the result used Claude's best-guess beverage_type_guess.
    needs_beverage_confirmation : bool
        True when the label-only check could not resolve a beverage type and
        is waiting for the agent to confirm before requirements are evaluated.
        When True, ``checks`` is empty and the frontend should show a
        confirmation prompt.
    """

    filenames: list[str]
    overall_status: Status
    beverage_type: Optional[str] = None
    beverage_type_confirmed: bool = False
    needs_beverage_confirmation: bool = False
    checks: list[FieldResult]
    extracted: ExtractedLabelData
    processing_time_ms: int
    error: Optional[str] = None
