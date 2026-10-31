"""Stable error contract for review and label-check responses.

Every user-facing error or non-pass *system* response carries:
- error_code: machine-readable token (see docs/ERROR_CODES.md)
- message: scrubbed, human-readable text
- request_id: per-request correlation id (also sent as X-Request-ID)

Photo-quality failures additionally carry a ``retake`` object so field users
know which panel to recapture and why. Compliance ``fail`` remains a rule
violation and is not remapped through this module.
"""

from __future__ import annotations

import contextvars
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

PHOTO_QUALITY_LOW = "PHOTO_QUALITY_LOW"
PHOTO_UNREADABLE_FIELD = "PHOTO_UNREADABLE_FIELD"
UNSUPPORTED_FILE = "UNSUPPORTED_FILE"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
RATE_LIMITED = "RATE_LIMITED"
UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
UPSTREAM_ERROR = "UPSTREAM_ERROR"
INTERNAL_ERROR = "INTERNAL_ERROR"
INVALID_REQUEST = "INVALID_REQUEST"
AUTH_REQUIRED = "AUTH_REQUIRED"
VISION_UNAVAILABLE = "VISION_UNAVAILABLE"
# Aliases kept so older clients and docs still resolve.
CONFIG_ERROR = VISION_UNAVAILABLE
MODEL_UNAVAILABLE = VISION_UNAVAILABLE

# User copy for vision/config/upstream failures. Never mention the provider,
# env var, or key material. Only photo-quality codes say "retake".
VISION_UNAVAILABLE_MESSAGE = (
    "The label reader is unavailable right now — your photo is fine. "
    "Try again shortly."
)
CONFIG_ERROR_MESSAGE = VISION_UNAVAILABLE_MESSAGE
MODEL_UNAVAILABLE_MESSAGE = VISION_UNAVAILABLE_MESSAGE
UPSTREAM_TIMEOUT_MESSAGE = (
    "The label reader timed out — your photo is fine. Try again shortly."
)
UPSTREAM_ERROR_MESSAGE = (
    "The label reader is unavailable right now — your photo is fine. "
    "Try again shortly."
)
RATE_LIMITED_UPSTREAM_MESSAGE = (
    "The label reader is busy — your photo is fine. Wait a moment and try again."
)

# These codes leave the review pipeline as a non-2xx structured body.
# They must never appear as overall_status pass/fail.
VISION_HTTP_CODES = frozenset({
    VISION_UNAVAILABLE,
    UPSTREAM_TIMEOUT,
    UPSTREAM_ERROR,
    RATE_LIMITED,
})
SERVICE_UNAVAILABLE_CODES = VISION_HTTP_CODES

RETAKE_MESSAGE = "Photo quality is too low, please retake."
BLANK_IMAGE_MESSAGE = (
    "The submitted image appears to be blank. Please upload a photo of an "
    "alcohol beverage label."
)
BLANK_IMAGE_TIP = (
    "No label is visible in the frame. Photograph the printed label so "
    "text is readable."
)
NOT_A_LABEL_MESSAGE = (
    "The submitted image does not appear to be an alcohol beverage "
    "label. This tool only reviews labels for beer, wine, malt "
    "beverages, and distilled spirits. Please resubmit with a photo "
    "of an alcohol beverage label."
)

RETAKE_CODES = frozenset({PHOTO_QUALITY_LOW, PHOTO_UNREADABLE_FIELD})

RetakeArea = Literal["front", "back", "neck"]
RetakeCause = Literal[
    "blur",
    "glare",
    "blank",
    "dark",
    "angle",
    "curvature",
    "cropped",
    "low_resolution",
]
FieldPresence = Literal["present", "absent", "unreadable"]

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,64}$")

# Required TTB label elements used for field_status / retake targeting.
REQUIRED_ELEMENTS: tuple[str, ...] = (
    "brand_name",
    "class_type",
    "alcohol_content",
    "net_contents",
    "government_warning",
    "name_and_address",
)

FIELD_DISPLAY_NAMES: dict[str, str] = {
    "brand_name": "brand",
    "class_type": "class/type",
    "alcohol_content": "ABV",
    "net_contents": "net contents",
    "government_warning": "warning",
    "government_warning_header": "warning",
    "government_warning_body": "warning",
    "name_and_address": "name and address",
    "country_of_origin": "country of origin",
    "sulfite_declaration": "sulfite declaration",
    "allergen_statements": "allergen statements",
    "age_statement": "age statement",
    "commodity_statement": "commodity statement",
    "label": "label",
}

FIELD_AREAS: dict[str, RetakeArea] = {
    "brand_name": "front",
    "class_type": "front",
    "alcohol_content": "front",
    "net_contents": "front",
    "name_and_address": "front",
    "country_of_origin": "back",
    "government_warning": "back",
    "government_warning_header": "back",
    "government_warning_body": "back",
    "sulfite_declaration": "back",
    "allergen_statements": "back",
    "age_statement": "back",
    "commodity_statement": "back",
    "label": "front",
}

CAUSE_TIPS: dict[str, str] = {
    "blur": "Hold the phone still and tap the label to focus before you take the photo.",
    "glare": "Hold the phone parallel to the label and avoid overhead light glare.",
    "blank": BLANK_IMAGE_TIP,
    "dark": "Move to brighter, even lighting or turn on a room light behind the camera.",
    "angle": "Hold the phone parallel to the label so the text is not skewed.",
    "curvature": "Flatten the label in the frame or photograph the curved panel straight-on.",
    "cropped": "Include the entire required statement in the frame, then retake.",
    "low_resolution": "Move closer to the label and retake at full camera resolution.",
}

_SAFE_FALLBACK = "An unexpected error occurred. Please try again."

# Patterns that must never appear in a user-facing message.
_SCRUB_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)traceback \(most recent call last\):.*", re.DOTALL),
    re.compile(r"(?i)(?:file|path)\s+[\"']?(?:/|[A-Za-z]:\\)[^\s\"']+"),
    re.compile(r"(?i)/[A-Za-z0-9._\-/]+(?:\.(?:py|ts|tsx|js|json|log|pem|key))"),
    re.compile(r"(?i)sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"(?i)(?:api[_-]?key|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)claude-(?:sonnet|haiku|opus)[A-Za-z0-9.\-]*"),
    re.compile(r"(?i)anthropic|openai|vertex|bedrock"),
    re.compile(r"(?i)request_id['\"]?\s*[:=]\s*['\"]?req_[A-Za-z0-9]+"),
)

_log = logging.getLogger(__name__)

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ttb_request_id", default=""
)


class RetakeGuidance(BaseModel):
    """Actionable recapture guidance when a required field could not be read."""

    field: str = Field(description="Required element that could not be read.")
    area: RetakeArea = Field(description="Label panel to recapture: front, back, or neck.")
    cause: RetakeCause = Field(
        description="Why the photo could not be read: blur, glare, dark, angle, curvature, cropped, or low_resolution."
    )
    tip: str = Field(description="Short plain-language advice for the next photo.")


@dataclass
class ClientError:
    """Structured pipeline error returned instead of a raw exception string."""

    code: str
    message: str
    retake: Optional[RetakeGuidance] = None
    quality: Optional[object] = None

    def __str__(self) -> str:
        return self.message


@dataclass
class UnreadableField:
    """One required (or observed) element that could not be read confidently."""

    field: str
    score: float
    threshold: float
    area: RetakeArea = "front"
    cause: RetakeCause = "blur"
    tip: str = CAUSE_TIPS["blur"]


@dataclass
class ReadabilityAnalysis:
    """Per-element presence plus retake guidance for unreadable fields."""

    field_status: dict[str, FieldPresence] = field(default_factory=dict)
    unreadable: list[UnreadableField] = field(default_factory=list)
    retake: Optional[RetakeGuidance] = None
    error_code: Optional[str] = None

    @property
    def has_required_unreadable(self) -> bool:
        # country_of_origin is skipped when its score is exactly 0.0 (domestic
        # labels). Any other unreadable origin score must still force a retake
        # so we do not silently drop the imported-origin check.
        return any(
            item.field in REQUIRED_ELEMENTS or item.field == "country_of_origin"
            for item in self.unreadable
        )


def generate_request_id() -> str:
    """Return a new opaque request id (UUID4 hex)."""
    return uuid.uuid4().hex


def sanitize_request_id(incoming: str | None) -> str:
    """Accept a client ``X-Request-ID`` only when it is a short safe token.

    Allowed: ``^[A-Za-z0-9-]{1,64}$``. Anything else (empty, spaces, slashes,
    punctuation, oversize) is discarded and a fresh id is generated.
    """
    value = (incoming or "").strip()
    if value and REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return generate_request_id()


def set_request_id(request_id: str) -> contextvars.Token[str]:
    return _request_id_var.set(request_id)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _request_id_var.reset(token)


def current_request_id() -> str:
    """Return the request id for this task, generating one if absent."""
    rid = _request_id_var.get()
    if rid:
        return rid
    rid = generate_request_id()
    _request_id_var.set(rid)
    return rid


def display_field_name(field: str) -> str:
    return FIELD_DISPLAY_NAMES.get(field, field.replace("_", " "))


def area_for_field(field: str) -> RetakeArea:
    return FIELD_AREAS.get(field, "front")


def tip_for_cause(cause: str) -> str:
    return CAUSE_TIPS.get(cause, CAUSE_TIPS["blur"])


def infer_retake_cause(notes: str | None = None, *, default: RetakeCause = "blur") -> RetakeCause:
    """Infer a retake cause from already-available extraction notes.

    Does not inspect pixels or call a model — notes are produced by the same
    extraction that already ran.
    """
    text = (notes or "").lower()
    if any(token in text for token in ("blank", "empty frame", "nothing printed", "washed out")):
        return "blank"
    if any(token in text for token in ("glare", "reflection", "specular", "flash bounce")):
        return "glare"
    if any(token in text for token in ("dark", "too dark", "underexposed", "shadow")):
        return "dark"
    if any(token in text for token in ("blur", "focus", "out of focus", "soft")):
        return "blur"
    if any(token in text for token in ("angle", "skew", "tilt", "foreshorten")):
        return "angle"
    if any(token in text for token in ("curv", "rounded", "bottle wrap")):
        return "curvature"
    if any(token in text for token in ("crop", "cut off", "cutoff", "clipped", "partial")):
        return "cropped"
    if any(token in text for token in ("resolution", "pixel", "too small", "low res")):
        return "low_resolution"
    return default


def build_retake(
    field: str,
    cause: RetakeCause,
    *,
    area: RetakeArea | None = None,
    tip: str | None = None,
) -> RetakeGuidance:
    canonical = (
        "government_warning"
        if field in {"government_warning_header", "government_warning_body"}
        else field
    )
    return RetakeGuidance(
        field=display_field_name(canonical),
        area=area or area_for_field(canonical),
        cause=cause,
        tip=tip or tip_for_cause(cause),
    )


def scrub_user_message(message: str | None, *, fallback: str = _SAFE_FALLBACK) -> str:
    """Strip stack traces, paths, model ids, and provider bodies from a message."""
    if not message:
        return fallback
    cleaned = str(message).strip()
    if not cleaned:
        return fallback
    for pattern in _SCRUB_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = " ".join(cleaned.split())
    if not cleaned or len(cleaned) < 8:
        return fallback
    # A leftover traceback marker or path slash after scrubbing is still unsafe.
    lowered = cleaned.lower()
    if "traceback" in lowered or "sk-ant-" in lowered:
        return fallback
    return cleaned


def error_payload(
    code: str,
    message: str,
    *,
    request_id: str | None = None,
    retake: RetakeGuidance | None = None,
    extra: dict | None = None,
) -> dict:
    """JSON body for HTTP error responses (keeps ``detail`` for compatibility)."""
    rid = request_id or current_request_id()
    safe = scrub_user_message(message)
    body: dict = {
        "error_code": code,
        "message": safe,
        "request_id": rid,
        "detail": safe,
    }
    if retake is not None:
        body["retake"] = retake.model_dump()
    if extra:
        body.update(extra)
    return body


class RequestIdLogFilter(logging.Filter):
    """Prefix log lines with request_id=... when a request is in flight."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = _request_id_var.get() or "-"
        record.request_id = rid
        msg = record.getMessage()
        # Structured RequestTiming JSON already embeds request_id. Prefix
        # only free-form lines so log parsers stay intact.
        if rid != "-" and "request_id" not in msg and "RequestTiming:" not in msg:
            record.msg = f"request_id={rid} {record.msg}"
        return True


def install_request_id_logging() -> None:
    """Attach the request-id filter to application loggers (idempotent)."""
    filt = RequestIdLogFilter()
    for name in (
        "app",
        "app.main",
        "app.compliance",
        "app.claude_client",
        "app.limiter",
        "app.errors",
        "app.security",
        "app.auth",
        "app.request_limits",
        "app.cv_typesize",
    ):
        logger = logging.getLogger(name)
        if not any(isinstance(existing, RequestIdLogFilter) for existing in logger.filters):
            logger.addFilter(filt)
