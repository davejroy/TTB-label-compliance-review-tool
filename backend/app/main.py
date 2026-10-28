"""FastAPI application: HTTP routes for the TTB Label Compliance Review Tool.

Three endpoint families are exposed under ``/api``:
- ``/api/review`` and ``/api/review/batch`` - compare label image(s) against
  COLA application data (``run_compliance_checks``).
- ``/api/label-check/batch`` - validate label image(s) against TTB mandatory
  label requirements with no application data (``check_label_requirements``).
- ``/api/health`` - basic liveness check.

Per NFR-2 (docs/PDR.md), nothing is persisted: uploaded images are read into
memory, sent to Claude for extraction, and discarded once the response is
returned.

Performance notes:
- File reads within a single review are done concurrently with asyncio.gather.
- Batch requests run all per-label reviews concurrently.
- Multi-photo independent extractions run concurrently with asyncio.gather
  when distinct photo roles are supplied.
- CPU-bound Pillow work is offloaded to a thread pool via run_in_threadpool.
- Images are prepared (validated, enhanced, downscaled) once in _read_and_validate_file
  and the processed bytes are passed directly to extract_label_fields with
  preprocessed=True, avoiding a second Pillow pass.
- Transient Claude API errors (rate-limit, overloaded, timeout) are retried
  once after a short delay before returning an error to the client.
- Structured request timing and latency metrics are logged for all review routes.
"""

import asyncio
from dataclasses import dataclass, field
import json
import os
import random
import re
import time
import logging
from urllib.parse import urlparse

from pydantic import ValidationError

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from .claude_client import (
    ExtractionCallMetrics,
    ImageQualityError,
    _is_heif_bytes,
    extract_label_fields,
    extract_label_fields_with_usage,
    open_image_for_intake,
    original_bytes_for_typesize,
    prepare_image,
)
from . import claude_client as _claude_client
from .compliance import (
    LowConfidenceError,
    UNCONFIRMED_BEVERAGE_TYPE,
    _combined_warning_text,
    analyze_readability,
    check_label_requirements,
    merge_extracted_label_data,
    overall_status,
    run_compliance_checks,
)
from .errors import (
    AUTH_REQUIRED,
    BLANK_IMAGE_MESSAGE,
    BLANK_IMAGE_TIP,
    FILE_TOO_LARGE,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    PHOTO_QUALITY_LOW,
    RATE_LIMITED,
    REQUEST_ID_HEADER,
    RETAKE_CODES,
    RETAKE_MESSAGE,
    UNSUPPORTED_FILE,
    UPSTREAM_ERROR,
    UPSTREAM_TIMEOUT,
    ClientError,
    build_retake,
    current_request_id,
    error_payload,
    install_request_id_logging,
    sanitize_request_id,
    scrub_user_message,
    set_request_id,
)
from .cv_typesize import evaluate_type_size_from_image_bytes
from .models import (
    ApplicationData,
    CalibrationMethod,
    ExtractedLabelData,
    ImageQualityAdvisory,
    LabelCheckResult,
    ReviewResult,
    StageTimings,
    TypeSizeMeasurement,
)
from .quality import (
    QualityAssessment,
    assess_image_quality,
    get_pregate_mode,
    merge_quality_assessments,
    quality_retake,
)
from .auth import require_demo_access, auth_enabled
from .limiter import (
    DailyCapExceeded,
    daily_cap_exceeded_handler,
    get_client_ip,
    get_review_rate_limit,
    limiter,
    raise_if_daily_capped,
    rate_limit_exceeded_handler,
    reserve_validated_review,
    require_daily_cap,
    set_client_ip,
)
from .request_limits import (
    FILE_TOO_LARGE_MESSAGE,
    IMAGE_COUNTS_LENGTH_MESSAGE,
    IMAGE_COUNTS_MISMATCH_MESSAGE,
    MAX_FILE_SIZE,
    MAX_IMAGES_PER_LABEL,
    RequestBodyLimitMiddleware,
    TEMPLATE_TOO_LARGE_MESSAGE,
    file_too_large_error,
    get_max_template_bytes,
    read_upload_capped,
    validate_label_upload_request,
)
from .security import SecurityHeadersMiddleware, is_production_runtime
from .prefill import (
    ALL_COLA_PRESETS,
    parse_application_template,
)

# Module-level logger. In production configure JSON handler for log aggregators.
_log = logging.getLogger(__name__)

# Concurrency cap for Claude extraction calls:
# Process-wide semaphore prevents unbounded-parallel batch requests from overwhelming the Anthropic API.
# Configurable via MAX_CONCURRENT_CLAUDE_CALLS (default 5).
_CLAUDE_SEMAPHORE_LIMIT = int(os.environ.get("MAX_CONCURRENT_CLAUDE_CALLS", "5"))
_claude_semaphore = asyncio.Semaphore(_CLAUDE_SEMAPHORE_LIMIT)

# Concurrency acquisition timeout (seconds). If the semaphore cannot be acquired within this window,
# return a friendly 503/429 busy message instead of timing out silently or raising an auth error.
_CLAUDE_SEMAPHORE_TIMEOUT_S = float(os.environ.get("CLAUDE_CONCURRENCY_TIMEOUT", "30.0"))


def get_claude_semaphore() -> asyncio.Semaphore:
    """Return the active process-wide Claude extraction concurrency semaphore."""
    return _claude_semaphore


def set_claude_semaphore_limit(limit: int) -> None:
    """Helper for runtime configuration or test fixtures to adjust semaphore capacity."""
    global _claude_semaphore, _CLAUDE_SEMAPHORE_LIMIT
    _CLAUDE_SEMAPHORE_LIMIT = limit
    _claude_semaphore = asyncio.Semaphore(limit)


def _warning_text_for_typesize(extracted: ExtractedLabelData) -> str | None:
    """Join extracted Government Warning header and body for local 16.22 estimates."""
    text = _combined_warning_text(
        extracted.government_warning_header,
        extracted.government_warning_body,
    )
    return text or None


def _log_request_timing(
    endpoint: str,
    wall_time_ms: int,
    claude_time_ms: int,
    claude_calls: int,
    image_count: int,
    status: str,
    extra: dict | None = None,
) -> None:
    """Emit structured latency and timing log for review operations.

    Logs non-sensitive operational metrics (durations, counts, statuses)
    without logging any image bytes, file contents, or authorization secrets.
    """
    payload = {
        "event": "request_timing",
        "endpoint": endpoint,
        "wall_time_ms": wall_time_ms,
        "claude_time_ms": claude_time_ms,
        "claude_calls": claude_calls,
        "image_count": image_count,
        "status": status,
        "request_id": current_request_id(),
    }
    if extra:
        payload.update(extra)
    _log.info("RequestTiming: %s", json.dumps(payload))


def api_docs_enabled() -> bool:
    """True when interactive OpenAPI docs should be mounted (off by default)."""
    return os.environ.get("ENABLE_API_DOCS", "").strip().lower() in {"1", "true", "yes", "on"}


_API_DOCS = api_docs_enabled()
app = FastAPI(
    title="TTB Label Compliance Review Tool",
    docs_url="/docs" if _API_DOCS else None,
    redoc_url="/redoc" if _API_DOCS else None,
    openapi_url="/openapi.json" if _API_DOCS else None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_exception_handler(DailyCapExceeded, daily_cap_exceeded_handler)
install_request_id_logging()


# Curated, user-safe HTTP messages. Parser/exception text is never used.
_FIXED_HTTP_MESSAGES: dict[int, str] = {
    400: "The request could not be processed.",
    401: "Invalid or missing access token.",
    413: FILE_TOO_LARGE_MESSAGE,
    422: "The request could not be processed.",
    429: "Too many requests. Please wait a moment and try again.",
    500: "An unexpected error occurred. Please try again.",
    503: "The review service is temporarily unavailable. Please try again.",
    504: "The review service timed out. Please try again.",
}

# Messages we raise ourselves (never interpolated exception text).
_SAFE_HTTP_DETAILS: frozenset[str] = frozenset(
    {
        "Invalid or missing access token.",
        "The request could not be processed.",
        FILE_TOO_LARGE_MESSAGE,
        TEMPLATE_TOO_LARGE_MESSAGE,
        "image_counts must be a JSON list of integers.",
        IMAGE_COUNTS_MISMATCH_MESSAGE,
        IMAGE_COUNTS_LENGTH_MESSAGE,
        "Daily review limit reached. Please try again tomorrow.",
        "Too many failed sign-in attempts. Please wait a few minutes and try again.",
        "Too many requests. Please wait a moment and try again.",
        "Too many requests. Please wait a moment before submitting additional label reviews.",
        "The application data was not valid.",
        "The batch request was not valid.",
        "The template file could not be parsed.",
        "The template file could not be read.",
        "Too many labels in this batch. Please submit fewer labels and try again.",
        "Each label must include at least one image.",
        f"Upload between 1 and {MAX_IMAGES_PER_LABEL} images per label.",
        (
            f"A maximum of {MAX_IMAGES_PER_LABEL} images may be uploaded per label. "
            "Please resubmit with 4 or fewer photos."
        ),
    }
)


def _http_error_code(status_code: int) -> str:
    if status_code == 401:
        return AUTH_REQUIRED
    if status_code == 413:
        return FILE_TOO_LARGE
    if status_code == 429:
        return RATE_LIMITED
    if status_code in (400, 409, 422):
        return INVALID_REQUEST
    if status_code in (503, 504):
        return UPSTREAM_ERROR
    if status_code >= 500:
        return INTERNAL_ERROR
    return INVALID_REQUEST


_NAMED_VALIDATION_DETAIL = re.compile(
    r"^Invalid (?:application data|batch request): field '[a-z_]+' is missing or invalid\.$"
)


def _http_error_message(status_code: int, detail) -> str:
    if isinstance(detail, str) and (
        detail in _SAFE_HTTP_DETAILS or _NAMED_VALIDATION_DETAIL.fullmatch(detail)
    ):
        return detail
    return _FIXED_HTTP_MESSAGES.get(status_code, "The request could not be processed.")


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Map missing/malformed multipart fields onto the INVALID_REQUEST contract."""
    request_id = getattr(request.state, "request_id", None) or current_request_id()
    _log.info("Request validation failed: %s", exc.errors())  # details stay in logs only
    field = "request"
    for err in exc.errors():
        for part in err.get("loc") or ():
            name = str(part)
            if name in _APPLICATION_FIELD_NAMES:
                field = name
                break
        if field != "request":
            break
    message = (
        f"Invalid application data: field '{field}' is missing or invalid."
        if field in _APPLICATION_FIELD_NAMES
        else "The request could not be processed."
    )
    return JSONResponse(
        status_code=422,
        content=error_payload(
            INVALID_REQUEST,
            message,
            request_id=request_id,
            extra={"detail": message, "field": field},
        ),
        headers={REQUEST_ID_HEADER: request_id},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Return the stable error contract for HTTPException (keeps ``detail``)."""
    request_id = getattr(request.state, "request_id", None) or current_request_id()
    if isinstance(exc.detail, str):
        _log.info("HTTP %s: %s", exc.status_code, exc.detail)
    message = _http_error_message(exc.status_code, exc.detail)
    headers = {REQUEST_ID_HEADER: request_id}
    if exc.headers:
        headers.update(exc.headers)
    extra: dict = {}
    if exc.status_code == 429:
        extra["error"] = "too_many_requests"
        retry_after = (exc.headers or {}).get("Retry-After")
        if retry_after:
            extra["retry_after"] = int(retry_after)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(
            _http_error_code(exc.status_code),
            message,
            request_id=request_id,
            extra=extra or None,
        ),
        headers=headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak stack traces, paths, or provider bodies to the client."""
    request_id = getattr(request.state, "request_id", None) or current_request_id()
    _log.exception("Unhandled error")
    return JSONResponse(
        status_code=500,
        content=error_payload(
            INTERNAL_ERROR,
            "An unexpected error occurred. Please try again.",
            request_id=request_id,
        ),
        headers={REQUEST_ID_HEADER: request_id},
    )


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Generate a request_id, echo X-Request-ID, and tag subsequent log lines."""
    incoming = request.headers.get(REQUEST_ID_HEADER)
    request_id = sanitize_request_id(incoming)
    request.state.request_id = request_id
    set_request_id(request_id)
    try:
        set_client_ip(get_client_ip(request))
    except Exception:  # noqa: BLE001
        _log.exception("Client IP resolution failed; continuing fail-open")
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response

# Default known frontend origins. Keep the production allowlist from prod #25:
# local Vite plus the Render frontend declared in render.yaml. Do not add
# private -dev or preview hosts here.
DEFAULT_CORS_ORIGINS: list[str] = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://ttb-label-frontend.onrender.com",
]

# Preferred first, then names used by earlier deploys.
CORS_ENV_VARS: tuple[str, ...] = (
    "ALLOWED_ORIGINS",
    "CORS_ALLOWED_ORIGINS",
    "CORS_ORIGINS",
)


def _is_valid_cors_origin(origin: str) -> bool:
    """Validate whether an origin string is a syntactically valid CORS origin.

    A valid CORS origin must be:
    - '*' (wildcard), OR
    - An http or https origin with scheme and host (optional port), and no path,
      query string, or fragment (a trailing slash is considered invalid for a CORS origin).
    """
    if origin == "*":
        return True
    try:
        parsed = urlparse(origin)
    except Exception:  # noqa: BLE001
        return False

    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    # Ensure no path (except empty), no query, and no fragment
    if parsed.path not in ("", "/") or (parsed.path == "/" and origin.endswith("/")):
        return False
    if parsed.query or parsed.params or parsed.fragment:
        return False
    return True


def _default_cors() -> tuple[list[str], str | None]:
    """Exact origin allowlist only — no wildcard host regex (F13)."""
    return list(DEFAULT_CORS_ORIGINS), None


def _refuse_wildcard(reason: str) -> tuple[list[str], str | None]:
    """Never emit ``*`` in production; warn whenever a wildcard was requested."""
    _log.warning("%s", reason)
    if is_production_runtime():
        _log.warning(
            "CORS wildcard '*' is not allowed in production; using the default frontend allowlist."
        )
        return _default_cors()
    return ["*"], None


def _resolve_cors_env() -> tuple[str | None, str | None]:
    """Return (var_name, raw_value) for the first CORS env var that is set."""
    for var_name in CORS_ENV_VARS:
        if var_name in os.environ:
            return var_name, os.environ[var_name]
    return None, None


def get_cors_configuration() -> tuple[list[str], str | None]:
    """Resolve CORS origins from ALLOWED_ORIGINS (and compatible aliases).

    Fail-open means a parse/store error uses the known frontend allowlist so
    legitimate browsers are never locked out. Production never returns ``*``;
    a wildcard from the environment is replaced with the default allowlist and
    a startup warning is logged.
    """
    try:
        selected_var, raw_value = _resolve_cors_env()

        if selected_var is None:
            _log.info(
                "No CORS override env var set. Using default known frontend origins: %s",
                DEFAULT_CORS_ORIGINS,
            )
            return _default_cors()

        trimmed_value = (raw_value or "").strip()

        if not trimmed_value:
            _log.warning(
                "%s is set but empty/whitespace. Using the default frontend allowlist "
                "(never falling back to '*').",
                selected_var,
            )
            return _default_cors()

        if trimmed_value == "*":
            return _refuse_wildcard(
                f"{selected_var} is set to explicit wildcard '*'."
            )

        raw_items = [item.strip() for item in (raw_value or "").split(",") if item.strip()]
        if not raw_items:
            _log.warning(
                "%s contained only delimiters (%r). Using the default frontend allowlist "
                "(never falling back to '*').",
                selected_var,
                raw_value,
            )
            return _default_cors()

        if "*" in raw_items:
            return _refuse_wildcard(f"{selected_var} contains wildcard '*'.")

        valid_origins: list[str] = []
        for item in raw_items:
            if _is_valid_cors_origin(item):
                valid_origins.append(item)
            else:
                _log.warning(
                    "Invalid CORS origin '%s' in %s dropped (must be http/https with host, no path/query).",
                    item,
                    selected_var,
                )

        if not valid_origins:
            _log.warning(
                "No valid CORS origins found in %s (%r). Using the default frontend allowlist "
                "(never falling back to '*').",
                selected_var,
                raw_value,
            )
            return _default_cors()

        return valid_origins, None
    except Exception:  # noqa: BLE001
        _log.exception(
            "CORS configuration failed; using the default frontend allowlist (fail-open)"
        )
        return _default_cors()


# Initialize CORS middleware on the FastAPI app
_cors_origins, _cors_origin_regex = get_cors_configuration()
if _cors_origins == ["*"]:
    _log.warning(
        "CORS is configured to allow all origins ('*'). This must not be used in production."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_origin_regex,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=[REQUEST_ID_HEADER],
)
# Security headers apply to CORS preflight and streaming bodies alike.
app.add_middleware(SecurityHeadersMiddleware)
# Outermost: reject oversized bodies before multipart parsing. Never blocks /api/health.
app.add_middleware(RequestBodyLimitMiddleware)

# Retry config for transient Claude API errors.
# One app-level retry with jitter. Anthropic SDK retries are disabled (max_retries=0)
# so one image set costs at most two model calls (F9).
_RETRY_DELAY_S = 2.0
_RETRY_JITTER_S = 0.5
_RETRYABLE = (RateLimitError, APITimeoutError, APIConnectionError)


def _retry_delay_seconds() -> float:
    """Bounded delay plus jitter so concurrent retries do not thundering-herd."""
    return _RETRY_DELAY_S + random.uniform(0.0, _RETRY_JITTER_S)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/demo-info")
def demo_info() -> dict:
    """Public login-screen metadata. Returns only whether the access gate is on.

    Never returns a username, token, or other account identifier (F15).
    """
    return {"auth_enabled": auth_enabled()}


@app.get("/api/cola/presets")
def list_cola_presets() -> list[dict]:
    """Return built-in COLA application sample presets for one-click demos."""
    return ALL_COLA_PRESETS


@app.post("/api/cola/parse-template", dependencies=[Depends(require_demo_access)])
async def parse_cola_template(file: UploadFile = File(...)) -> list[ApplicationData]:
    """Parse an uploaded CSV or JSON file into validated ApplicationData objects.

    Requires the same demo-access dependency as review routes (F21). Template
    bodies are capped at 1 MB. Parser details stay in server logs; clients
    receive a fixed message.
    """
    try:
        content, exceeded = await read_upload_capped(file, get_max_template_bytes())
    except Exception as exc:  # noqa: BLE001
        _log.info("Could not read template file '%s': %s", file.filename, exc)
        raise HTTPException(
            status_code=422,
            detail="The template file could not be read.",
        ) from exc
    if exceeded:
        raise HTTPException(status_code=413, detail=TEMPLATE_TOO_LARGE_MESSAGE)

    try:
        results = await run_in_threadpool(
            parse_application_template,
            content,
            file.filename or "template.csv",
        )
    except ValueError as exc:
        _log.info("Template parse validation error for '%s': %s", file.filename, exc)
        raise HTTPException(
            status_code=422,
            detail="The template file could not be parsed.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        _log.warning("Unexpected error parsing template '%s': %s", file.filename, exc)
        raise HTTPException(
            status_code=422,
            detail="The template file could not be parsed.",
        ) from exc

    return results


# Accepted image container signatures (magic bytes). Validating the actual
# file content — not the client-supplied MIME type or extension — prevents a
# spoofed/renamed non-image file from reaching the image pipeline.
_IMAGE_SIGNATURES: tuple[bytes, ...] = (
    b"\xff\xd8\xff",          # JPEG
    b"\x89PNG\r\n\x1a\n",   # PNG
    b"GIF87a",                 # GIF
    b"GIF89a",                 # GIF
    b"BM",                     # BMP
    b"II*\x00",                # TIFF (little-endian)
    b"MM\x00*",                # TIFF (big-endian)
)

IMAGE_COUNTS_ERROR = "image_counts must be a JSON list of integers."
_APPLICATION_FIELD_NAMES = frozenset(
    {
        "application",
        "applications",
        "beverage_type",
        "brand_name",
        "class_type",
        "alcohol_content",
        "net_contents",
        "name_and_address",
        "country_of_origin",
        "image_counts",
        "confirmed_beverage_type",
        "photo_roles",
        "files",
        "file",
    }
)


def _validation_field_name(exc: Exception, default: str) -> str:
    if isinstance(exc, ValidationError):
        for err in exc.errors():
            for part in err.get("loc") or ():
                name = str(part)
                if name in _APPLICATION_FIELD_NAMES:
                    return name
            loc = err.get("loc") or ()
            if loc:
                return str(loc[-1])
    return default


def _named_application_error(field: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=f"Invalid application data: field '{field}' is missing or invalid.",
    )


def _named_batch_error(field: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=f"Invalid batch request: field '{field}' is missing or invalid.",
    )


def _parse_application_json(raw: str, field_name: str = "application") -> ApplicationData:
    """Parse one ApplicationData form field and name the invalid property."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise _named_application_error(field_name) from exc
    if not isinstance(payload, dict):
        raise _named_application_error(field_name)
    try:
        return ApplicationData(**payload)
    except ValidationError as exc:
        raise _named_application_error(_validation_field_name(exc, field_name)) from exc


def _parse_application_list(raw: str) -> list[ApplicationData]:
    """Parse the batch ``applications`` form field and name the invalid property."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise _named_batch_error("applications") from exc
    if not isinstance(payload, list):
        raise _named_batch_error("applications")
    parsed: list[ApplicationData] = []
    for item in payload:
        if not isinstance(item, dict):
            raise _named_batch_error("applications")
        try:
            parsed.append(ApplicationData(**item))
        except ValidationError as exc:
            raise _named_batch_error(_validation_field_name(exc, "applications")) from exc
    return parsed


def _reject_if_not_image(data: bytes, filename: str) -> str | None:
    """Return a user-facing error if 'data' is not a recognized image, else None.

    WEBP ('RIFF'....'WEBP') and HEIC/HEIF (ISO-BMFF ``ftyp`` + brand) are
    handled as special cases. HEIC is decoded later via pillow-heif.
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return None
    if _is_heif_bytes(data):
        return None
    if any(data.startswith(sig) for sig in _IMAGE_SIGNATURES):
        return None
    _log.info("Rejected non-image upload '%s' (unrecognized signature).", filename)
    return (
        f"File '{filename}' is not a supported image. Please upload a JPEG, "
        f"PNG, WEBP, GIF, BMP, TIFF, HEIC, or HEIF photo of the label."
    )


def _parse_image_counts(raw: str) -> list[int]:
    """Parse the batch ``image_counts`` form field without leaking JSON errors."""
    try:
        counts = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        _log.info("Invalid image_counts: %s", exc)
        raise HTTPException(status_code=422, detail=IMAGE_COUNTS_ERROR) from exc
    if not isinstance(counts, list) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in counts
    ):
        _log.info("Invalid image_counts: parsed value is not a list of integers")
        raise HTTPException(status_code=422, detail=IMAGE_COUNTS_ERROR)
    return counts


class _StructuredUploadError(Exception):
    """Carry a ClientError out of the streaming batch loop."""

    def __init__(self, error: ClientError) -> None:
        self.error = error
        super().__init__(error.message)


def _client_error_from_exception(exc: Exception) -> ClientError:
    """Map an extraction/validation exception to a scrubbed ClientError."""
    if isinstance(exc, _StructuredUploadError):
        return exc.error
    if isinstance(exc, DailyCapExceeded):
        return ClientError(RATE_LIMITED, "Daily review limit reached. Please try again tomorrow.")
    if isinstance(exc, ImageQualityError):
        return ClientError(exc.error_code, exc.user_message, retake=exc.retake)
    if isinstance(exc, AuthenticationError):
        _log.error("Upstream authentication failed: %s", exc)
        return ClientError(
            UPSTREAM_ERROR,
            "The review service is temporarily unavailable. Please try again.",
        )
    if isinstance(exc, RateLimitError):
        _log.warning("Upstream rate limit: %s", exc)
        return ClientError(RATE_LIMITED, "The review service is busy. Please try again shortly.")
    if isinstance(exc, APITimeoutError):
        _log.warning("Upstream timeout: %s", exc)
        return ClientError(UPSTREAM_TIMEOUT, "The review service timed out. Please try again.")
    if isinstance(exc, APIStatusError):
        _log.warning("Upstream status %s: %s", exc.status_code, exc)
        if exc.status_code in (429, 529):
            return ClientError(RATE_LIMITED, "The review service is busy. Please try again shortly.")
        return ClientError(
            UPSTREAM_ERROR,
            "The review service is temporarily unavailable. Please try again.",
        )
    if isinstance(exc, APIConnectionError):
        _log.warning("Upstream connection error: %s", exc)
        return ClientError(
            UPSTREAM_ERROR,
            "The review service is temporarily unavailable. Please try again.",
        )
    if isinstance(exc, RuntimeError) and "busy" in str(exc).lower():
        return ClientError(
            RATE_LIMITED,
            "The review service is currently busy. Please wait a moment and try again.",
        )
    if isinstance(exc, ValueError):
        return ClientError(INVALID_REQUEST, scrub_user_message(str(exc)))
    _log.exception("Unhandled extraction error: %s", exc)
    return ClientError(
        INTERNAL_ERROR,
        "An error occurred while reviewing this label. Please submit a new photo or try again.",
    )


def _quality_advisory(quality: QualityAssessment | None) -> ImageQualityAdvisory | None:
    if quality is None or get_pregate_mode() == "off":
        return None
    return quality.advisory()


def _quality_cause(quality: QualityAssessment | None):
    if quality is None:
        return None
    return quality.retake_cause


def _review_error_result(
    filenames: list[str],
    wall_ms: int,
    client_error: ClientError,
    extracted: ExtractedLabelData | None = None,
    field_status: dict | None = None,
    quality: QualityAssessment | None = None,
    timings: StageTimings | None = None,
) -> ReviewResult:
    is_retake = client_error.code in RETAKE_CODES
    return ReviewResult(
        filenames=filenames,
        overall_status="retake" if is_retake else "error",
        fields=[],
        extracted=extracted or ExtractedLabelData(),
        processing_time_ms=wall_ms,
        error=client_error.message,
        error_code=client_error.code,
        request_id=current_request_id(),
        retake=client_error.retake,
        field_status=field_status or {},
        quality=_quality_advisory(quality),
        timings_ms=timings,
    )


def _label_check_error_result(
    filenames: list[str],
    wall_ms: int,
    client_error: ClientError,
    extracted: ExtractedLabelData | None = None,
    photo_sources: list[str] | None = None,
    field_status: dict | None = None,
    beverage_type: str | None = None,
    beverage_type_confirmed: bool = False,
    quality: QualityAssessment | None = None,
    timings: StageTimings | None = None,
) -> LabelCheckResult:
    is_retake = client_error.code in RETAKE_CODES
    return LabelCheckResult(
        filenames=filenames,
        overall_status="retake" if is_retake else "error",
        checks=[],
        extracted=extracted or ExtractedLabelData(),
        processing_time_ms=wall_ms,
        error=client_error.message,
        error_code=client_error.code,
        request_id=current_request_id(),
        retake=client_error.retake,
        field_status=field_status or {},
        photo_sources=photo_sources or [],
        beverage_type=beverage_type,
        beverage_type_confirmed=beverage_type_confirmed,
        quality=_quality_advisory(quality),
        timings_ms=timings,
    )


@dataclass
class PreparedImage:
    """One upload after EXIF transpose, quality pre-gate, and model encode."""

    processed_bytes: bytes
    original_bytes: bytes
    filename: str
    quality: QualityAssessment | None = None
    decode_ms: int = 0
    pre_gate_ms: int = 0
    resize_ms: int = 0


def _intake_one_image(image_bytes: bytes, filename: str) -> PreparedImage | ClientError:
    """CPU-bound intake: EXIF transpose, quality pre-gate, then encode.

    Extreme quality failures return a ClientError so the caller never starts
    the vision request. Moderate issues stay on the PreparedImage.
    """
    decode_started = time.monotonic()
    try:
        img = open_image_for_intake(image_bytes, filename)
    except ImageQualityError as exc:
        _log.info("Image quality rejected for '%s': %s", filename, exc.user_message)
        return ClientError(exc.error_code, exc.user_message, retake=exc.retake)
    except ValueError as exc:
        _log.warning("Decode error for '%s': %s", filename, exc)
        return ClientError(
            PHOTO_QUALITY_LOW,
            RETAKE_MESSAGE,
            retake=build_retake("label", "blur"),
        )
    decode_ms = int((time.monotonic() - decode_started) * 1000)

    quality = None
    pregate_started = time.monotonic()
    if get_pregate_mode() != "off":
        quality = assess_image_quality(img)
        if quality.should_block and quality.block_cause:
            _log.info(
                "Quality pre-gate blocked '%s' cause=%s resolution=%sx%s",
                filename,
                quality.block_cause,
                quality.width,
                quality.height,
            )
            blank = quality.exposure == "bright" and quality.block_cause == "glare"
            return ClientError(
                PHOTO_QUALITY_LOW,
                BLANK_IMAGE_MESSAGE if blank else RETAKE_MESSAGE,
                retake=quality_retake(
                    quality.block_cause,
                    tip=BLANK_IMAGE_TIP if blank else None,
                ),
                quality=quality,
            )
    pre_gate_ms = int((time.monotonic() - pregate_started) * 1000)

    resize_started = time.monotonic()
    try:
        processed_bytes, _media_type = prepare_image(
            image_bytes, filename, image=img, quality=quality
        )
        original_bytes = original_bytes_for_typesize(img, image_bytes)
    except ImageQualityError as exc:
        _log.info("Image quality rejected for '%s': %s", filename, exc.user_message)
        return ClientError(exc.error_code, exc.user_message, retake=exc.retake)
    except ValueError as exc:
        _log.warning("Decode error for '%s': %s", filename, exc)
        return ClientError(
            PHOTO_QUALITY_LOW,
            RETAKE_MESSAGE,
            retake=build_retake("label", "blur"),
        )
    resize_ms = int((time.monotonic() - resize_started) * 1000)
    return PreparedImage(
        processed_bytes=processed_bytes,
        original_bytes=original_bytes,
        filename=filename,
        quality=quality,
        decode_ms=decode_ms,
        pre_gate_ms=pre_gate_ms,
        resize_ms=resize_ms,
    )


async def _read_and_validate_file(
    file: UploadFile,
) -> PreparedImage | ClientError:
    """Read one uploaded file, validate it, and return prepared intake data."""
    image_bytes, exceeded = await read_upload_capped(file, MAX_FILE_SIZE)
    if exceeded:
        return file_too_large_error(file.filename)
    sig_error = _reject_if_not_image(image_bytes, file.filename or "label.jpg")
    if sig_error:
        return ClientError(UNSUPPORTED_FILE, sig_error)
    return await run_in_threadpool(
        _intake_one_image,
        image_bytes,
        file.filename or "label.jpg",
    )


async def _read_images(
    files: list[UploadFile],
    filenames: list[str],
) -> list[PreparedImage] | ClientError:
    """Read and validate all uploaded files concurrently.

    Returns a list of PreparedImage on success, or a ClientError describing
    the first file that failed validation or the quality pre-gate.
    """
    validated = validate_label_upload_request(files)
    if validated.error:
        _log.warning("Upload rejected before read: %s", validated.error.message)
        return validated.error
    read_results = await asyncio.gather(*[_read_and_validate_file(f) for f in files])
    images: list[PreparedImage] = []
    for result in read_results:
        if isinstance(result, ClientError):
            return result
        images.append(result)
    return images


@dataclass
class ExtractionMetrics:
    """Holds timing and call counts for an extraction operation."""
    claude_time_ms: int = 0
    claude_calls: int = 0
    decode_ms: int = 0
    pre_gate_ms: int = 0
    resize_ms: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    model_ttft_ms: int = 0
    model_generation_ms: int = 0
    images: list[tuple[bytes, str]] = field(default_factory=list)
    original_images: list[tuple[bytes, str]] = field(default_factory=list)
    quality: QualityAssessment | None = None

    def add_call_metrics(self, call) -> None:
        """Fold one Anthropic call's cache tokens and TTFT into the review."""
        self.cache_creation_input_tokens += int(getattr(call, "cache_creation_input_tokens", 0) or 0)
        self.cache_read_input_tokens += int(getattr(call, "cache_read_input_tokens", 0) or 0)
        ttft = getattr(call, "ttft_ms", None)
        generation = getattr(call, "generation_ms", None)
        if ttft is not None:
            self.model_ttft_ms = ttft if self.model_ttft_ms == 0 else min(self.model_ttft_ms, int(ttft))
        if generation is not None:
            self.model_generation_ms += int(generation)


def _stage_timings(metrics: ExtractionMetrics, compliance_ms: int = 0) -> StageTimings:
    """Build the additive timings_ms payload from collected stage clocks."""
    return StageTimings(
        decode=metrics.decode_ms,
        pre_gate=metrics.pre_gate_ms,
        resize=metrics.resize_ms,
        model=metrics.claude_time_ms,
        compliance=compliance_ms,
        cache_creation_input_tokens=metrics.cache_creation_input_tokens,
        cache_read_input_tokens=metrics.cache_read_input_tokens,
        model_ttft_ms=metrics.model_ttft_ms,
        model_generation_ms=metrics.model_generation_ms,
    )


def _log_stage_timings(timings: StageTimings | None) -> None:
    """Emit one StageTimings INFO line. Fail-open: logging must not break review."""
    if timings is None:
        return
    try:
        payload = {
            "event": "stage_timings",
            "decode": timings.decode,
            "pre_gate": timings.pre_gate,
            "resize": timings.resize,
            "model": timings.model,
            "compliance": timings.compliance,
            "cache_creation_input_tokens": timings.cache_creation_input_tokens,
            "cache_read_input_tokens": timings.cache_read_input_tokens,
            "model_ttft_ms": timings.model_ttft_ms,
            "model_generation_ms": timings.model_generation_ms,
            "request_id": current_request_id(),
        }
        _log.info("StageTimings: %s", json.dumps(payload))
    except Exception:  # noqa: BLE001
        _log.debug("Failed to emit stage timings", exc_info=True)


def _timing_extra(timings: StageTimings | None, extra: dict | None = None) -> dict:
    payload = dict(extra or {})
    if timings is not None:
        payload["timings_ms"] = timings.model_dump()
    return payload


def _run_extract_sync(images: list[tuple[bytes, str]]):
    """Run extraction in a worker thread.

    Production uses ``extract_label_fields_with_usage`` so cache tokens and
    TTFT are recorded. Unit tests that patch ``app.main.extract_label_fields``
    keep working because that name is consulted first.
    """
    if extract_label_fields is not _claude_client.extract_label_fields:
        return extract_label_fields(images, True), ExtractionCallMetrics()
    return extract_label_fields_with_usage(images, True)


async def _extract_fields_with_retry(
    images: list[tuple[bytes, str]],
    metrics: ExtractionMetrics | None = None,
) -> tuple[ExtractedLabelData | None, ClientError | None]:
    """Call extract_label_fields with concurrency gating and one automatic retry on transient errors.

    Images are passed as pre-processed JPEG bytes (preprocessed=True) so
    Claude client skips the Pillow pipeline entirely.

    Returns ``(extracted, None)`` on success or ``(None, ClientError)`` on
    failure after exhausting retries.
    """
    try:
        await asyncio.wait_for(_claude_semaphore.acquire(), timeout=_CLAUDE_SEMAPHORE_TIMEOUT_S)
    except asyncio.TimeoutError:
        _log.warning(
            "Claude concurrency limit reached (capacity=%d, timeout=%.1fs). Rejecting with busy message.",
            _CLAUDE_SEMAPHORE_LIMIT,
            _CLAUDE_SEMAPHORE_TIMEOUT_S,
        )
        return (
            None,
            ClientError(
                RATE_LIMITED,
                "The review service is currently processing a high volume of label requests. "
                "Please wait a moment and resubmit.",
            ),
        )

    try:
        for attempt in range(2):
            call_start = time.monotonic()
            try:
                if metrics is not None:
                    metrics.claude_calls += 1
                extracted, call_metrics = await run_in_threadpool(
                    _run_extract_sync, images
                )
                if metrics is not None:
                    metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
                    metrics.add_call_metrics(call_metrics)
                return extracted, None
            except _RETRYABLE as exc:
                if metrics is not None:
                    metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
                if attempt == 0:
                    await asyncio.sleep(_retry_delay_seconds())
                    continue
                return None, _client_error_from_exception(exc)
            except APIStatusError as exc:
                if metrics is not None:
                    metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
                if attempt == 0 and exc.status_code == 529:
                    await asyncio.sleep(_retry_delay_seconds())
                    continue
                return None, _client_error_from_exception(exc)
            except Exception as exc:  # noqa: BLE001
                if metrics is not None:
                    metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
                return None, _client_error_from_exception(exc)
        return None, ClientError(INTERNAL_ERROR, "An unexpected error occurred. Please try again.")
    finally:
        _claude_semaphore.release()



async def _extract_fields(
    files: list[UploadFile],
    filenames: list[str],
    photo_roles: list[str] | None = None,
    metrics: ExtractionMetrics | None = None,
) -> tuple[ExtractedLabelData | None, ClientError | None, list[str]]:
    """Shared image-read + Claude-extraction pipeline with multi-photo merging.

    When photo_roles identifies multiple distinct panels (e.g. "front" and "back"),
    each photo is extracted concurrently via asyncio.gather and merged via
    merge_extracted_label_data.

    Returns (merged_extraction, ClientError | None, effective_roles).
    """
    validated = validate_label_upload_request(files)
    if validated.error:
        return None, validated.error, []

    images_or_error = await _read_images(files, filenames)
    if isinstance(images_or_error, ClientError):
        if metrics is not None and isinstance(images_or_error.quality, QualityAssessment):
            metrics.quality = images_or_error.quality
        return None, images_or_error, []

    prepared: list[PreparedImage] = []
    for item in images_or_error:
        if isinstance(item, PreparedImage):
            prepared.append(item)
            continue
        processed_bytes, filename = item
        prepared.append(
            PreparedImage(
                processed_bytes=processed_bytes,
                original_bytes=processed_bytes,
                filename=filename,
            )
        )
    images: list[tuple[bytes, str]] = [
        (item.processed_bytes, item.filename) for item in prepared
    ]
    if metrics is not None:
        metrics.images = images
        metrics.original_images = [
            (item.original_bytes, item.filename) for item in prepared
        ]
        metrics.quality = merge_quality_assessments(
            [item.quality for item in prepared if item.quality is not None]
        )
        metrics.decode_ms += sum(item.decode_ms for item in prepared)
        metrics.pre_gate_ms += sum(item.pre_gate_ms for item in prepared)
        metrics.resize_ms += sum(item.resize_ms for item in prepared)

    # Atomically reserve daily budget after validation, before the model call.
    reserve_validated_review(1)

    effective_roles: list[str] = photo_roles or []
    unique_roles = set(effective_roles) if effective_roles else set()
    do_per_role = (
        len(images) > 1
        and len(effective_roles) == len(images)
        and len(unique_roles) > 1
    )

    if not do_per_role:
        extracted, error_msg = await _extract_fields_with_retry(images, metrics=metrics)
        return extracted, error_msg, effective_roles

    role_metrics = [ExtractionMetrics() for _ in images]
    gather_results = await asyncio.gather(
        *[
            _extract_fields_with_retry([img], metrics=m)
            for img, m in zip(images, role_metrics)
        ]
    )

    if metrics is not None:
        for rm in role_metrics:
            metrics.claude_calls += rm.claude_calls
            metrics.claude_time_ms += rm.claude_time_ms
            metrics.cache_creation_input_tokens += rm.cache_creation_input_tokens
            metrics.cache_read_input_tokens += rm.cache_read_input_tokens
            if rm.model_ttft_ms:
                metrics.model_ttft_ms = (
                    rm.model_ttft_ms
                    if metrics.model_ttft_ms == 0
                    else min(metrics.model_ttft_ms, rm.model_ttft_ms)
                )
            metrics.model_generation_ms += rm.model_generation_ms

    extractions: list[ExtractedLabelData] = []
    for (ext, err), role in zip(gather_results, effective_roles):
        if err or ext is None:
            if isinstance(err, ClientError):
                return None, err, effective_roles
            return (
                None,
                ClientError(UPSTREAM_ERROR, f"Extraction failed for {role} photo."),
                effective_roles,
            )
        extractions.append(ext)

    merged = await run_in_threadpool(merge_extracted_label_data, extractions, effective_roles)
    return merged, None, effective_roles

async def _evaluate_type_size(
    images: list[tuple[bytes, str]],
    extracted: ExtractedLabelData,
    net_contents: str | None,
    log_context: str,
) -> TypeSizeMeasurement | None:
    """Measure 27 CFR 16.22 type-size on the original photos (working copy is capped)."""
    type_size_details: TypeSizeMeasurement | None = None
    if not images:
        return None
    gw_locs = [
        loc
        for loc in extracted.field_locations
        if getattr(loc, "field", None) == "government_warning"
    ]
    gw_bbox = (
        (gw_locs[0].x, gw_locs[0].y, gw_locs[0].width, gw_locs[0].height)
        if gw_locs
        else None
    )
    warning_text = _warning_text_for_typesize(extracted)

    async def _measure_one(img_bytes: bytes, img_name: str) -> TypeSizeMeasurement:
        try:
            return await run_in_threadpool(
                evaluate_type_size_from_image_bytes,
                img_bytes,
                filename=img_name,
                net_contents=net_contents,
                gw_bbox=gw_bbox,
                warning_text=warning_text,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("Type-size evaluation error in %s for '%s': %s", log_context, img_name, exc)
            return TypeSizeMeasurement(
                method=CalibrationMethod.UNCALIBRATED_ESTIMATE,
                required_min_height_mm=2.0,
                state="cannot_measure",
                verification_note=(
                    f"Type-size evaluation encountered an unexpected condition "
                    f"({type(exc).__name__}). Physical gauge measurement recommended."
                ),
            )

    if len(images) == 1:
        results = [await _measure_one(*images[0])]
    else:
        results = list(
            await asyncio.gather(*[_measure_one(data, name) for data, name in images])
        )
    for ts_res in results:
        if ts_res.method not in (CalibrationMethod.UNCALIBRATED_ESTIMATE, CalibrationMethod.NONE):
            return ts_res
        if type_size_details is None:
            type_size_details = ts_res
    return type_size_details


async def _review_single(files: list[UploadFile], application: ApplicationData) -> ReviewResult:
    """Run an application-vs-label review for one label's image(s)."""
    start = time.monotonic()
    filenames = [f.filename or "unknown" for f in files]
    metrics = ExtractionMetrics()

    extracted, error_msg, _roles = await _extract_fields(files, filenames, metrics=metrics)

    if error_msg:
        wall_time_ms = int((time.monotonic() - start) * 1000)
        timings = _stage_timings(metrics)
        _log_stage_timings(timings)
        _log_request_timing(
            endpoint="/api/review",
            wall_time_ms=wall_time_ms,
            claude_time_ms=metrics.claude_time_ms,
            claude_calls=metrics.claude_calls,
            image_count=len(files),
            status="retake" if error_msg.code in RETAKE_CODES else "error",
            extra=_timing_extra(timings),
        )
        return _review_error_result(
            filenames,
            wall_time_ms,
            error_msg,
            quality=error_msg.quality if isinstance(error_msg.quality, QualityAssessment) else metrics.quality,
            timings=timings,
        )

    # 27 CFR 16.22 Option A — measure on the original full-resolution photo.
    compliance_started = time.monotonic()
    type_size_source = metrics.original_images or metrics.images
    type_size_details = await _evaluate_type_size(
        type_size_source,
        extracted,
        application.net_contents or extracted.net_contents,
        "_review_single",
    )
    quality_cause = _quality_cause(metrics.quality)

    try:
        fields = run_compliance_checks(
            application,
            extracted,
            type_size_details=type_size_details,
            quality_cause=quality_cause,
        )
    except LowConfidenceError as exc:
        timings = _stage_timings(
            metrics, int((time.monotonic() - compliance_started) * 1000)
        )
        wall_time_ms = int((time.monotonic() - start) * 1000)
        _log_stage_timings(timings)
        _log_request_timing(
            endpoint="/api/review",
            wall_time_ms=wall_time_ms,
            claude_time_ms=metrics.claude_time_ms,
            claude_calls=metrics.claude_calls,
            image_count=len(files),
            status="retake",
            extra=_timing_extra(
                timings, {"error_type": "LowConfidenceError", "error_code": exc.error_code}
            ),
        )
        return ReviewResult(
            filenames=filenames,
            overall_status="retake",
            fields=[],
            extracted=extracted,
            processing_time_ms=wall_time_ms,
            error=RETAKE_MESSAGE,
            error_code=exc.error_code,
            request_id=current_request_id(),
            retake=exc.retake,
            field_status=exc.field_status,
            quality=_quality_advisory(metrics.quality),
            timings_ms=timings,
        )
    analysis = analyze_readability(
        extracted,
        quality_cause=quality_cause,
        beverage_type=application.beverage_type,
    )
    status = overall_status(fields)
    error = None
    error_code = None
    retake = None
    if analysis.has_required_unreadable:
        status = "retake"
        error = RETAKE_MESSAGE
        error_code = analysis.error_code
        retake = analysis.retake
    timings = _stage_timings(
        metrics, int((time.monotonic() - compliance_started) * 1000)
    )
    wall_time_ms = int((time.monotonic() - start) * 1000)
    _log_stage_timings(timings)
    _log_request_timing(
        endpoint="/api/review",
        wall_time_ms=wall_time_ms,
        claude_time_ms=metrics.claude_time_ms,
        claude_calls=metrics.claude_calls,
        image_count=len(files),
        status=status,
        extra=_timing_extra(timings),
    )
    return ReviewResult(
        filenames=filenames,
        overall_status=status,
        fields=fields,
        extracted=extracted,
        processing_time_ms=wall_time_ms,
        error=error,
        error_code=error_code,
        request_id=current_request_id(),
        retake=retake,
        field_status=analysis.field_status,
        quality=_quality_advisory(metrics.quality),
        timings_ms=timings,
    )


@app.post(
    "/api/review",
    response_model=ReviewResult,
    dependencies=[Depends(require_demo_access), Depends(require_daily_cap)],
)
@limiter.limit(get_review_rate_limit)
async def review_label(
    request: Request,
    files: list[UploadFile] = File(...),
    application: str = Form(...),
) -> ReviewResult:
    """Single-label review: 1-4 label images plus one JSON-encoded
    ApplicationData form field."""
    application_data = _parse_application_json(application)
    return await _review_single(files, application_data)


@app.post(
    "/api/review/batch",
    response_model=list[ReviewResult],
    dependencies=[Depends(require_demo_access), Depends(require_daily_cap)],
)
@limiter.limit(get_review_rate_limit)
async def review_labels_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    image_counts: str = Form(...),
    applications: str = Form(...),
) -> list[ReviewResult]:
    """Batch review: all labels' images concatenated in files."""
    batch_start = time.monotonic()
    counts = _parse_image_counts(image_counts)
    application_list = _parse_application_list(applications)

    validated = validate_label_upload_request(
        files,
        image_counts=counts,
        expected_label_count=len(application_list),
        enforce_batch_cap=True,
    )
    if validated.error:
        raise HTTPException(status_code=422, detail=validated.error.message)
    raise_if_daily_capped(request, additional=validated.label_count)

    results: list[ReviewResult] = await asyncio.gather(
        *[
            _review_single(lf, ad)
            for lf, ad in zip(validated.label_groups, application_list)
        ]
    )

    batch_wall_ms = int((time.monotonic() - batch_start) * 1000)
    _log_request_timing(
        endpoint="/api/review/batch",
        wall_time_ms=batch_wall_ms,
        claude_time_ms=sum(r.processing_time_ms for r in results),
        claude_calls=len(results),
        image_count=len(files),
        status="success",
        extra={"label_count": len(results)},
    )
    return list(results)


@app.post(
    "/api/review/batch/stream",
    dependencies=[Depends(require_demo_access), Depends(require_daily_cap)],
)
@limiter.limit(get_review_rate_limit)
async def review_labels_batch_stream(
    request: Request,
    files: list[UploadFile] = File(...),
    image_counts: str = Form(...),
    applications: str = Form(...),
) -> StreamingResponse:
    """Streaming batch review — yields NDJSON result lines as each label completes.

    This endpoint reviews labels concurrently (under the existing Claude
    semaphore) and streams each result as a newline-terminated JSON object
    the moment it is ready, rather than waiting for all labels to finish.
    Clients can begin rendering results immediately.

    Response format: Content-Type: application/x-ndjson
    Each line is a complete JSON object with either a ReviewResult payload or
    an error object: {"index": N, "result": {...}} or {"index": N, "error": "..."}

    A sentinel line {"done": true, "total": N} is emitted after all results.
    """
    batch_start = time.monotonic()
    counts = _parse_image_counts(image_counts)
    application_list = _parse_application_list(applications)

    # F8: count checks (including MAX_IMAGES_PER_LABEL) run before any file read.
    validated = validate_label_upload_request(
        files,
        image_counts=counts,
        expected_label_count=len(application_list),
        enforce_batch_cap=True,
    )
    if validated.error:
        raise HTTPException(status_code=422, detail=validated.error.message)
    raise_if_daily_capped(request, additional=validated.label_count)

    # Read all file bytes eagerly before entering the async generator.
    # UploadFile objects can only be read once; we must do it before streaming
    # begins so the generator can operate without awaiting on request state.
    file_data_groups: list[list[tuple[bytes, str]]] = []
    for group in validated.label_groups:
        group_data = []
        for uploaded in group:
            raw, exceeded = await read_upload_capped(uploaded, MAX_FILE_SIZE)
            if exceeded:
                raise HTTPException(status_code=413, detail=FILE_TOO_LARGE_MESSAGE)
            group_data.append((raw, uploaded.filename or "label.jpg"))
        file_data_groups.append(group_data)

    async def _stream_one(idx: int, file_data: list[tuple[bytes, str]], app_data: ApplicationData) -> dict:
        start = time.monotonic()
        claude_call_start = 0
        claude_call_duration_ms = 0
        success = False
        try:
                prepared_images: list[PreparedImage] = []
                for raw_bytes, fname in file_data:
                    if len(raw_bytes) > MAX_FILE_SIZE:
                        raise _StructuredUploadError(
                            ClientError(FILE_TOO_LARGE, f"File '{fname}' exceeds 10 MB limit.")
                        )
                    sig_error = _reject_if_not_image(raw_bytes, fname)
                    if sig_error:
                        raise _StructuredUploadError(ClientError(UNSUPPORTED_FILE, sig_error))
                    intake = await run_in_threadpool(_intake_one_image, raw_bytes, fname)
                    if isinstance(intake, ClientError):
                        raise _StructuredUploadError(intake)
                    prepared_images.append(intake)

                processed_images = [
                    (item.processed_bytes, item.filename) for item in prepared_images
                ]
                original_images = [
                    (item.original_bytes, item.filename) for item in prepared_images
                ]
                stream_quality = merge_quality_assessments(
                    [item.quality for item in prepared_images if item.quality is not None]
                )
                quality_cause = _quality_cause(stream_quality)
                stream_metrics = ExtractionMetrics(
                    decode_ms=sum(item.decode_ms for item in prepared_images),
                    pre_gate_ms=sum(item.pre_gate_ms for item in prepared_images),
                    resize_ms=sum(item.resize_ms for item in prepared_images),
                )

                # Atomically reserve daily budget after intake, before the model call.
                reserve_validated_review(1)

                claude_call_start = time.monotonic()
                try:
                    await asyncio.wait_for(_claude_semaphore.acquire(), timeout=_CLAUDE_SEMAPHORE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    _log.warning(
                        "Streaming batch index %d: Claude concurrency limit reached (capacity=%d).",
                        idx,
                        _CLAUDE_SEMAPHORE_LIMIT,
                    )
                    raise RuntimeError("The review service is currently busy. Please wait a moment and try again.")
                try:
                    extracted, call_metrics = await run_in_threadpool(
                        _run_extract_sync,
                        processed_images,
                    )
                finally:
                    _claude_semaphore.release()

                claude_call_duration_ms = int((time.monotonic() - claude_call_start) * 1000)
                stream_metrics.claude_time_ms = claude_call_duration_ms
                stream_metrics.claude_calls = 1
                stream_metrics.add_call_metrics(call_metrics)

                compliance_started = time.monotonic()
                type_size_details = await _evaluate_type_size(
                    original_images or processed_images,
                    extracted,
                    app_data.net_contents or extracted.net_contents,
                    "stream",
                )

                try:
                    fields = run_compliance_checks(
                        app_data,
                        extracted,
                        type_size_details=type_size_details,
                        quality_cause=quality_cause,
                    )
                except LowConfidenceError as exc:
                    timings = _stage_timings(
                        stream_metrics, int((time.monotonic() - compliance_started) * 1000)
                    )
                    wall_ms = int((time.monotonic() - start) * 1000)
                    _log_stage_timings(timings)
                    _log_request_timing(
                        endpoint="/api/review/batch/stream",
                        wall_time_ms=wall_ms,
                        claude_time_ms=claude_call_duration_ms,
                        claude_calls=1,
                        image_count=len(processed_images),
                        status="retake",
                        extra=_timing_extra(
                            timings,
                            {
                                "batch_index": idx,
                                "error_type": "LowConfidenceError",
                                "error_code": exc.error_code,
                            },
                        ),
                    )
                    result = ReviewResult(
                        filenames=[fname for _, fname in processed_images],
                        overall_status="retake",
                        fields=[],
                        extracted=extracted,
                        processing_time_ms=wall_ms,
                        error=RETAKE_MESSAGE,
                        error_code=exc.error_code,
                        request_id=current_request_id(),
                        retake=exc.retake,
                        field_status=exc.field_status,
                        quality=_quality_advisory(stream_quality),
                        timings_ms=timings,
                    )
                    line = json.dumps({"index": idx, "result": result.model_dump()})
                else:
                    analysis = analyze_readability(
                        extracted,
                        quality_cause=quality_cause,
                        beverage_type=app_data.beverage_type,
                    )
                    status = overall_status(fields)
                    stream_error = None
                    stream_code = None
                    stream_retake = None
                    if analysis.has_required_unreadable:
                        status = "retake"
                        stream_error = RETAKE_MESSAGE
                        stream_code = analysis.error_code
                        stream_retake = analysis.retake
                    timings = _stage_timings(
                        stream_metrics, int((time.monotonic() - compliance_started) * 1000)
                    )
                    wall_ms = int((time.monotonic() - start) * 1000)
                    result = ReviewResult(
                        filenames=[fname for _, fname in processed_images],
                        overall_status=status,
                        fields=fields,
                        extracted=extracted,
                        processing_time_ms=wall_ms,
                        error=stream_error,
                        error_code=stream_code,
                        request_id=current_request_id(),
                        retake=stream_retake,
                        field_status=analysis.field_status,
                        quality=_quality_advisory(stream_quality),
                        timings_ms=timings,
                    )
                    success = True
                    _log_stage_timings(timings)
                    _log_request_timing(
                        endpoint="/api/review/batch/stream",
                        wall_time_ms=wall_ms,
                        claude_time_ms=claude_call_duration_ms,
                        claude_calls=1,
                        image_count=len(processed_images),
                        status=status,
                        extra=_timing_extra(timings, {"batch_index": idx}),
                    )
                    line = json.dumps({"index": idx, "result": result.model_dump()})
        except Exception as exc:  # noqa: BLE001
            _log.warning("Streaming batch error at index %d: %s", idx, exc)
            wall_ms = int((time.monotonic() - start) * 1000)
            _log_request_timing(
                endpoint="/api/review/batch/stream",
                wall_time_ms=wall_ms,
                claude_time_ms=claude_call_duration_ms,
                claude_calls=1 if claude_call_start > 0 else 0,
                image_count=len(file_data),
                status="error",
                extra={"batch_index": idx, "error": str(exc)},
            )
            client_error = _client_error_from_exception(exc)
            result = _review_error_result(
                [fname for _, fname in file_data],
                wall_ms,
                client_error,
                quality=client_error.quality if isinstance(client_error.quality, QualityAssessment) else None,
            )
            line = json.dumps({"index": idx, "result": result.model_dump()})
        return {
            "idx": idx,
            "line": line + "\n",
            "claude_ms": claude_call_duration_ms,
            "success": success,
        }

    async def generate():
        total_claude_time_ms = 0
        successful_labels = 0
        tasks = [
            asyncio.create_task(_stream_one(idx, file_data, app_data))
            for idx, (file_data, app_data) in enumerate(zip(file_data_groups, application_list))
        ]
        for finished in asyncio.as_completed(tasks):
            rec = await finished
            total_claude_time_ms += rec["claude_ms"]
            if rec["success"]:
                successful_labels += 1
            yield rec["line"]

        batch_wall_ms = int((time.monotonic() - batch_start) * 1000)
        _log_request_timing(
            endpoint="/api/review/batch/stream/summary",
            wall_time_ms=batch_wall_ms,
            claude_time_ms=total_claude_time_ms,
            claude_calls=len(file_data_groups),
            image_count=len(files),
            status="done",
            extra={"total_labels": len(file_data_groups), "successful_labels": successful_labels},
        )
        yield json.dumps({"done": True, "total": len(file_data_groups)}) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no"},  # disable Nginx buffering for real-time delivery
    )


async def _label_check_single(
    files: list[UploadFile],
    confirmed_beverage_type: str | None = None,
    photo_roles: list[str] | None = None,
) -> LabelCheckResult:
    """Run a label-only requirements check for one label's image(s).

    photo_roles: optional list of role strings ("front", "back", etc.).
    When roles differ, per-role extraction + merge is used.
    """
    start = time.monotonic()
    filenames = [f.filename or "unknown" for f in files]
    metrics = ExtractionMetrics()

    extracted, error_msg, effective_roles = await _extract_fields(
        files, filenames, photo_roles=photo_roles, metrics=metrics
    )

    if error_msg:
        wall_time_ms = int((time.monotonic() - start) * 1000)
        timings = _stage_timings(metrics)
        _log_stage_timings(timings)
        _log_request_timing(
            endpoint="/api/label-check",
            wall_time_ms=wall_time_ms,
            claude_time_ms=metrics.claude_time_ms,
            claude_calls=metrics.claude_calls,
            image_count=len(files),
            status="retake" if error_msg.code in RETAKE_CODES else "error",
            extra=_timing_extra(timings),
        )
        return _label_check_error_result(
            filenames,
            wall_time_ms,
            error_msg,
            photo_sources=effective_roles,
            quality=error_msg.quality if isinstance(error_msg.quality, QualityAssessment) else metrics.quality,
            timings=timings,
        )

    compliance_started = time.monotonic()
    type_size_source = metrics.original_images or metrics.images
    type_size_details = await _evaluate_type_size(
        type_size_source,
        extracted,
        extracted.net_contents,
        "_label_check_single",
    )
    quality_cause = _quality_cause(metrics.quality)

    type_confirmed = confirmed_beverage_type is not None
    try:
        checks = check_label_requirements(
            extracted,
            confirmed_beverage_type=confirmed_beverage_type,
            type_size_details=type_size_details,
            quality_cause=quality_cause,
        )
    except LowConfidenceError as exc:
        timings = _stage_timings(
            metrics, int((time.monotonic() - compliance_started) * 1000)
        )
        wall_time_ms = int((time.monotonic() - start) * 1000)
        _log_stage_timings(timings)
        _log_request_timing(
            endpoint="/api/label-check",
            wall_time_ms=wall_time_ms,
            claude_time_ms=metrics.claude_time_ms,
            claude_calls=metrics.claude_calls,
            image_count=len(files),
            status="retake",
            extra=_timing_extra(
                timings, {"error_type": "LowConfidenceError", "error_code": exc.error_code}
            ),
        )
        return LabelCheckResult(
            filenames=filenames,
            overall_status="retake",
            checks=[],
            extracted=extracted,
            beverage_type=confirmed_beverage_type or extracted.beverage_type_guess,
            beverage_type_confirmed=type_confirmed,
            processing_time_ms=wall_time_ms,
            error=RETAKE_MESSAGE,
            error_code=exc.error_code,
            request_id=current_request_id(),
            retake=exc.retake,
            field_status=exc.field_status,
            photo_sources=effective_roles,
            quality=_quality_advisory(metrics.quality),
            timings_ms=timings,
        )
    except ValueError as exc:
        if str(exc) == UNCONFIRMED_BEVERAGE_TYPE:
            timings = _stage_timings(
                metrics, int((time.monotonic() - compliance_started) * 1000)
            )
            wall_time_ms = int((time.monotonic() - start) * 1000)
            _log_stage_timings(timings)
            _log_request_timing(
                endpoint="/api/label-check",
                wall_time_ms=wall_time_ms,
                claude_time_ms=metrics.claude_time_ms,
                claude_calls=metrics.claude_calls,
                image_count=len(files),
                status="warning",
                extra=_timing_extra(timings, {"needs_confirmation": True}),
            )
            return LabelCheckResult(
                filenames=filenames,
                overall_status="warning",
                checks=[],
                extracted=extracted,
                beverage_type=extracted.beverage_type_guess,
                beverage_type_confirmed=False,
                needs_beverage_confirmation=True,
                processing_time_ms=wall_time_ms,
                photo_sources=effective_roles,
                request_id=current_request_id(),
                field_status=analyze_readability(
                    extracted,
                    quality_cause=quality_cause,
                    beverage_type=confirmed_beverage_type or extracted.beverage_type_guess,
                ).field_status,
                quality=_quality_advisory(metrics.quality),
                timings_ms=timings,
            )
        raise

    analysis = analyze_readability(
        extracted,
        quality_cause=quality_cause,
        beverage_type=confirmed_beverage_type or extracted.beverage_type_guess,
    )
    status = overall_status(checks)
    error = None
    error_code = None
    retake = None
    if analysis.has_required_unreadable:
        status = "retake"
        error = RETAKE_MESSAGE
        error_code = analysis.error_code
        retake = analysis.retake
    timings = _stage_timings(
        metrics, int((time.monotonic() - compliance_started) * 1000)
    )
    wall_time_ms = int((time.monotonic() - start) * 1000)
    _log_stage_timings(timings)
    _log_request_timing(
        endpoint="/api/label-check",
        wall_time_ms=wall_time_ms,
        claude_time_ms=metrics.claude_time_ms,
        claude_calls=metrics.claude_calls,
        image_count=len(files),
        status=status,
        extra=_timing_extra(timings),
    )
    return LabelCheckResult(
        filenames=filenames,
        overall_status=status,
        beverage_type=confirmed_beverage_type or extracted.beverage_type_guess,
        beverage_type_confirmed=type_confirmed,
        needs_beverage_confirmation=False,
        checks=checks,
        extracted=extracted,
        processing_time_ms=wall_time_ms,
        photo_sources=effective_roles,
        error=error,
        error_code=error_code,
        request_id=current_request_id(),
        retake=retake,
        field_status=analysis.field_status,
        quality=_quality_advisory(metrics.quality),
        timings_ms=timings,
    )


@app.post(
    "/api/label-check/batch",
    response_model=list[LabelCheckResult],
    dependencies=[Depends(require_demo_access), Depends(require_daily_cap)],
)
@limiter.limit(get_review_rate_limit)
async def label_check_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    image_counts: str = Form(...),
    confirmed_beverage_type: str = Form(default=""),
    photo_roles: str = Form(default=""),
) -> list[LabelCheckResult]:
    """Label-Only Check (batch). photo_roles: optional JSON array of role
    strings per file e.g. '["front","back"]' for per-panel extraction + merge.
    """
    batch_start = time.monotonic()
    counts = _parse_image_counts(image_counts)

    roles_list: list[str] = []
    if photo_roles.strip():
        try:
            parsed = json.loads(photo_roles)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = []
        if isinstance(parsed, list):
            roles_list = [item for item in parsed if isinstance(item, str)]

    validated = validate_label_upload_request(
        files,
        image_counts=counts,
        enforce_batch_cap=True,
    )
    if validated.error:
        raise HTTPException(status_code=422, detail=validated.error.message)
    raise_if_daily_capped(request, additional=validated.label_count)

    label_file_groups = validated.label_groups
    label_role_groups: list[list[str] | None] = []
    offset = 0
    for count in validated.image_counts:
        if roles_list and len(roles_list) >= offset + count:
            label_role_groups.append(roles_list[offset: offset + count])
        else:
            label_role_groups.append(None)
        offset += count

    results: list[LabelCheckResult] = await asyncio.gather(
        *[
            _label_check_single(
                lf,
                confirmed_beverage_type=confirmed_beverage_type or None,
                photo_roles=lr,
            )
            for lf, lr in zip(label_file_groups, label_role_groups)
        ]
    )

    batch_wall_ms = int((time.monotonic() - batch_start) * 1000)
    _log_request_timing(
        endpoint="/api/label-check/batch",
        wall_time_ms=batch_wall_ms,
        claude_time_ms=sum(r.processing_time_ms for r in results),
        claude_calls=len(results),
        image_count=len(files),
        status="success",
        extra={"label_count": len(results)},
    )
    return list(results)
