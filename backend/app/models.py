from typing import Literal, Optional

from pydantic import BaseModel, Field

BeverageType = Literal["distilled_spirits", "wine", "beer"]
Status = Literal["pass", "warning", "fail"]


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
    notes: Optional[str] = None


class FieldResult(BaseModel):
    field: str
    label_name: str
    status: Status
    application_value: Optional[str] = None
    label_value: Optional[str] = None
    message: str


class ReviewResult(BaseModel):
    filename: str
    overall_status: Status
    fields: list[FieldResult]
    extracted: ExtractedLabelData
    processing_time_ms: int
    error: Optional[str] = None
