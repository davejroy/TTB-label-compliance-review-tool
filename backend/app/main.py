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
import time
import logging

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from .claude_client import extract_label_fields, prepare_image, _media_type_for, ImageQualityError
from .compliance import (
    LowConfidenceError,
    UNCONFIRMED_BEVERAGE_TYPE,
    check_label_requirements,
    merge_extracted_label_data,
    overall_status,
    run_compliance_checks,
)
from .cv_typesize import evaluate_type_size_from_image_bytes
from .models import (
    ApplicationData,
    CalibrationMethod,
    ExtractedLabelData,
    LabelCheckResult,
    ReviewResult,
    TypeSizeMeasurement,
)
from .auth import require_demo_access, demo_username, auth_enabled
from .limiter import (
    limiter,
    rate_limit_exceeded_handler,
    get_review_rate_limit,
    daily_spend_guard,
)
from .prefill import (
    BUILTIN_COLA_PRESETS,
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
    }
    if extra:
        payload.update(extra)
    _log.info("RequestTiming: %s", json.dumps(payload))


app = FastAPI(title="TTB Label Compliance Review Tool")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# CORS - configure allowed origins via CORS_ORIGINS (comma-separated).
# Defaults to "*" (fail-open) so local dev and live frontend deployments
# work without mandatory configuration.
_cors_origins_env = os.environ.get("CORS_ORIGINS", "")
if not _cors_origins_env:
    _log.warning(
        "CORS_ORIGINS env var is not set — defaulting to '*' (allow all). "
        "Set CORS_ORIGINS to the frontend hostname before any deployment."
    )
_cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] if _cors_origins_env else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _apply_security_headers(response: Response, request: Request) -> Response:
    """Apply baseline HTTP security headers via setdefault (fail-open / non-breaking)."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).lower()
    if scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.middleware("http")
async def _security_headers_middleware(request: Request, call_next):
    """Ensure all responses include standard security headers without altering body or status."""
    response = await call_next(request)
    return _apply_security_headers(response, request)


MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB per image
MAX_IMAGES_PER_LABEL = 4
_ALLOWED_BEVERAGE_TYPES = {"distilled_spirits", "wine", "beer"}

# Retry config for transient Claude API errors.
# One retry after a short delay handles most rate-limit / 529 overloaded blips.
_RETRY_DELAY_S = 2.0
_RETRYABLE = (RateLimitError, APITimeoutError, APIConnectionError)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/demo-info")
def demo_info() -> dict:
    """Non-secret info for the login UI so evaluators are never locked out.

    Returns the expected demo username and whether the access gate is active.
    Never returns the token value itself.
    """
    return {"auth_enabled": auth_enabled(), "username": demo_username()}


@app.get("/api/cola/presets")
def list_cola_presets() -> list[dict]:
    """Return built-in COLA application sample presets for one-click demos."""
    return BUILTIN_COLA_PRESETS


@app.post("/api/cola/parse-template")
async def parse_cola_template(file: UploadFile = File(...)) -> list[ApplicationData]:
    """Parse an uploaded CSV or JSON file into validated ApplicationData objects.

    Returns HTTP 422 with actionable error detail on invalid structure, bad headers,
    missing mandatory fields, or malformed JSON/CSV. Never returns 401 or 500.
    """
    try:
        content = await file.read()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=422,
            detail=f"Could not read uploaded template file '{file.filename}': {exc}",
        ) from exc

    try:
        results = await run_in_threadpool(
            parse_application_template,
            content,
            file.filename or "template.csv",
        )
    except ValueError as exc:
        # User-fixable template input error -> HTTP 422 Unprocessable Entity
        _log.info("Template parse validation error for '%s': %s", file.filename, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        _log.warning("Unexpected error parsing template '%s': %s", file.filename, exc)
        raise HTTPException(
            status_code=422,
            detail=f"Error parsing template file '{file.filename}': {exc}",
        ) from exc

    return results


# Accepted image container signatures (magic bytes). Validating the actual
# file content — not the client-supplied MIME type or extension — prevents a
# spoofed/renamed non-image file from reaching the image pipeline.
_IMAGE_SIGNATURES: list[tuple[bytes, ...]] = [
    (b"\xff\xd8\xff",),               # JPEG / JFIF / EXIF
    (b"\x89PNG\r\n\x1a\n",),          # PNG
    (b"GIF87a", b"GIF89a"),            # GIF
    (b"BM",),                          # BMP
    (b"II*\x00", b"MM\x00*"),          # TIFF (little / big endian)
    (b"RIFF",),                        # WEBP (starts RIFF....WEBP)
]


def _is_image_bytes(data: bytes) -> bool:
    """Return True if data starts with a recognized image format signature."""
    if len(data) < 4:
        return False
    for sigs in _IMAGE_SIGNATURES:
        if any(data.startswith(sig) for sig in sigs):
            # Extra check for WEBP: bytes 8..12 must be 'WEBP'
            if data.startswith(b"RIFF"):
                return len(data) >= 12 and data[8:12] == b"WEBP"
            return True
    return False


async def _read_and_validate_file(
    file: UploadFile,
) -> tuple[bytes, str] | str:
    """Read an UploadFile, check size limit, validate magic bytes, and run
    Layer A image preparation (enhance contrast/sharpness + cap resolution).

    Returns (prepared_bytes, media_type) on success, or an error string on
    failure. Preparing the image here means the Pillow work happens once per
    upload rather than being repeated inside extract_label_fields.
    """
    try:
        content = await file.read()
    except Exception as exc:
        return f"Could not read {file.filename}: {exc}"

    if not content:
        return f"{file.filename} is empty."
    if len(content) > MAX_FILE_SIZE:
        return f"{file.filename} exceeds 10 MB limit ({len(content) / 1024 / 1024:.1f} MB)."
    if not _is_image_bytes(content):
        return f"{file.filename} is not a supported image file (JPEG, PNG, GIF, BMP, TIFF, WEBP)."

    # Offload Pillow preparation to thread pool so the event loop stays responsive
    try:
        prepared_bytes = await run_in_threadpool(prepare_image, content, file.filename or "")
        return prepared_bytes, _media_type_for(file.filename or "")
    except ImageQualityError as exc:
        # Surface the user-friendly message directly - no internal details.
        # HTTP 422 Unprocessable Entity signals a client-fixable input problem
        # (per RFC 9110 §15.5.21), distinct from 400 (bad request structure).
        _log.info("Image quality rejected for '%s': %s", file.filename, exc.user_message)
        return exc.user_message
    except ValueError as exc:
        return f"Could not process {file.filename}: {exc}"
    except Exception as exc:  # noqa: BLE001
        _log.warning("Image preparation unexpected error for '%s': %s", file.filename, exc)
        return f"Could not process {file.filename} as an image."


async def _read_and_validate_files(
    files: list[UploadFile],
) -> list[tuple[bytes, str]] | str:
    """Read and validate all files concurrently using asyncio.gather."""
    if not files:
        return "No files provided."
    if len(files) > MAX_IMAGES_PER_LABEL:
        return f"Too many images for a single label: got {len(files)}, maximum is {MAX_IMAGES_PER_LABEL}."

    results = await asyncio.gather(*[_read_and_validate_file(f) for f in files])
    for res in results:
        if isinstance(res, str):
            return res  # Return the first validation error encountered
    return results  # type: ignore[return-value]


@dataclass
class ExtractionMetrics:
    """Holds timing and call counts for an extraction operation."""
    claude_time_ms: int = 0
    claude_calls: int = 0
    images: list[tuple[bytes, str]] = field(default_factory=list)


_read_images = _read_and_validate_files


async def _extract_fields_with_retry(
    images: list[tuple[bytes, str]],
    metrics: ExtractionMetrics | None = None,
) -> tuple[ExtractedLabelData | None, str | None]:
    """Run extract_label_fields with concurrency gating and retry on transient Claude API errors.

    If MAX_CONCURRENT_CLAUDE_CALLS is reached, waits up to CLAUDE_CONCURRENCY_TIMEOUT
    seconds to acquire the semaphore before returning a busy message.
    """
    try:
        await asyncio.wait_for(_claude_semaphore.acquire(), timeout=_CLAUDE_SEMAPHORE_TIMEOUT_S)
    except asyncio.TimeoutError:
        _log.warning("Claude semaphore acquisition timed out after %.1fs", _CLAUDE_SEMAPHORE_TIMEOUT_S)
        return None, "System is currently busy processing other label reviews. Please try again in a few seconds."

    try:
        for attempt in range(2):
            call_start = time.monotonic()
            try:
                extracted = await run_in_threadpool(
                    extract_label_fields, images, preprocessed=True
                )
                if metrics is not None:
                    metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
                    metrics.claude_calls += 1
                return extracted, None
            except _RETRYABLE as exc:
                if metrics is not None:
                    metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
                    metrics.claude_calls += 1
                if attempt == 0:
                    _log.warning(
                        "Transient Claude error on attempt 1 (%s: %s) - retrying in %.1fs",
                        type(exc).__name__,
                        exc,
                        _RETRY_DELAY_S,
                    )
                    await asyncio.sleep(_RETRY_DELAY_S)
                    continue
                _log.error("Claude error on retry attempt 2: %s", exc)
                if isinstance(exc, RateLimitError):
                    return None, "Anthropic rate limit reached. Please try again shortly."
                if isinstance(exc, APITimeoutError):
                    return None, "The request to Claude timed out. Please try again."
                _log.warning("Claude connection error after retry: %s", exc)
                return None, "Network error contacting Claude API. Please try again shortly."
            except APIStatusError as exc:
                if metrics is not None:
                    metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
                    metrics.claude_calls += 1
                if exc.status_code == 401:
                    return None, "API key is invalid or missing. Check the ANTHROPIC_API_KEY environment variable."
                if exc.status_code == 429:
                    return None, "Anthropic rate limit reached. Please try again shortly."
                if exc.status_code in (500, 529):
                    return None, "Claude API is currently overloaded. Please try again shortly."
                _log.warning("Claude API error: %s", exc)
                return None, f"Claude API error ({exc.status_code}). Please try again."
            except AuthenticationError:
                return None, "API key is invalid or missing. Check the ANTHROPIC_API_KEY environment variable."
            except RateLimitError:
                if metrics is not None:
                    metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
                    metrics.claude_calls += 1
                return None, "Anthropic rate limit reached. Please try again shortly."
            except RuntimeError as exc:
                if metrics is not None:
                    metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
                if "busy" in str(exc).lower():
                    return None, str(exc)
                _log.warning("Runtime extraction error: %s", exc)
                return None, "Could not process label image(s). Please submit clearer photos."
            except Exception as exc:  # noqa: BLE001
                if metrics is not None:
                    metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
                _log.exception("Unexpected extraction error")
                return None, "Unexpected error during label extraction. Please try again."
        return None, "Unexpected retry loop exit."
    finally:
        _claude_semaphore.release()


async def _extract_fields(
    files: list[UploadFile],
    filenames: list[str],
    photo_roles: list[str] | None = None,
    metrics: ExtractionMetrics | None = None,
) -> tuple[ExtractedLabelData | None, str | None, list[str]]:
    """Validate files, execute extraction (single call or per-role concurrent
    calls merged), and return (extracted, error_msg, effective_roles).
    """
    images_or_error = await _read_images(files)
    if isinstance(images_or_error, str):
        return None, images_or_error, []

    images: list[tuple[bytes, str]] = images_or_error
    if metrics is not None:
        metrics.images = images

    effective_roles: list[str] = photo_roles or []
    unique_roles = set(effective_roles) if effective_roles else set()
    do_per_role = (
        len(images) > 1
        and len(effective_roles) == len(images)
        and len(unique_roles) > 1
    )

    if do_per_role:
        _log.info(
            "Multi-photo per-role extraction: %d photos with roles %s",
            len(images),
            effective_roles,
        )
        tasks = [
            _extract_fields_with_retry([img], metrics=metrics)
            for img in images
        ]
        results = await asyncio.gather(*tasks)
        for ext, err in results:
            if err:
                return None, err, effective_roles

        extractions = [ext for ext, _ in results if ext is not None]
        extracted = merge_extracted_label_data(extractions, effective_roles)
        return extracted, None, effective_roles

    extracted, error_msg = await _extract_fields_with_retry(images, metrics=metrics)
    return extracted, error_msg, effective_roles


async def _review_single(files: list[UploadFile], application: ApplicationData) -> ReviewResult:
    """Execute review for a single label: read files, extract fields with Claude,
    run 27 CFR 16.22 physical type-size verification, and run compliance matching.
    """
    start = time.monotonic()
    filenames = [f.filename or "unknown" for f in files]
    metrics = ExtractionMetrics()

    extracted, error_msg, _ = await _extract_fields(files, filenames, metrics=metrics)
    wall_time_ms = int((time.monotonic() - start) * 1000)

    if error_msg:
        _log_request_timing(
            endpoint="/api/review",
            wall_time_ms=wall_time_ms,
            claude_time_ms=metrics.claude_time_ms,
            claude_calls=metrics.claude_calls,
            image_count=len(files),
            status="error",
        )
        return ReviewResult(
            filenames=filenames,
            overall_status="fail",
            fields=[],
            extracted=ExtractedLabelData(),
            processing_time_ms=wall_time_ms,
            error=error_msg,
        )

    # 27 CFR 16.22 Option A Scale Marker Type-Size Verification
    type_size_details: TypeSizeMeasurement | None = None
    if metrics.images:
        gw_locs = [loc for loc in extracted.field_locations if getattr(loc, "field", None) == "government_warning"]
        gw_bbox = (gw_locs[0].x, gw_locs[0].y, gw_locs[0].width, gw_locs[0].height) if gw_locs else None

        for img_bytes, img_name in metrics.images:
            try:
                ts_res = await run_in_threadpool(
                    evaluate_type_size_from_image_bytes,
                    img_bytes,
                    filename=img_name,
                    net_contents=application.net_contents or extracted.net_contents,
                    gw_bbox=gw_bbox,
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning("Type-size evaluation error in _review_single for '%s': %s", img_name, exc)
                ts_res = TypeSizeMeasurement(
                    method=CalibrationMethod.UNCALIBRATED_ESTIMATE,
                    required_min_height_mm=2.0,
                    state="cannot_measure",
                    verification_note=f"Type-size evaluation encountered an unexpected condition ({type(exc).__name__}). Physical gauge measurement recommended.",
                )
            # If marker was found in this photo, use its measurement
            if ts_res.method not in (CalibrationMethod.UNCALIBRATED_ESTIMATE, CalibrationMethod.NONE):
                type_size_details = ts_res
                break
            if type_size_details is None:
                type_size_details = ts_res

    fields = run_compliance_checks(application, extracted, type_size_details=type_size_details)
    status = overall_status(fields)
    _log_request_timing(
        endpoint="/api/review",
        wall_time_ms=wall_time_ms,
        claude_time_ms=metrics.claude_time_ms,
        claude_calls=metrics.claude_calls,
        image_count=len(files),
        status=status,
    )
    return ReviewResult(
        filenames=filenames,
        overall_status=status,
        fields=fields,
        extracted=extracted,
        processing_time_ms=wall_time_ms,
    )


@app.post("/api/review", response_model=ReviewResult, dependencies=[Depends(require_demo_access)])
@limiter.limit(get_review_rate_limit)
async def review_label(
    request: Request,
    files: list[UploadFile] = File(...),
    application: str = Form(...),
) -> ReviewResult:
    """Single-label review: 1-4 label images plus one JSON-encoded
    ApplicationData form field."""
    daily_spend_guard.record_request(1)
    try:
        application_data = ApplicationData(**json.loads(application))
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("Invalid application data: %s", exc)
        raise HTTPException(status_code=422, detail="Invalid application data.") from exc
    return await _review_single(files, application_data)


@app.post("/api/review/batch", response_model=list[ReviewResult], dependencies=[Depends(require_demo_access)])
@limiter.limit(get_review_rate_limit)
async def review_labels_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    image_counts: str = Form(...),
    applications: str = Form(...),
) -> list[ReviewResult]:
    """Batch review: all labels' images concatenated in files."""
    batch_start = time.monotonic()
    try:
        counts: list[int] = json.loads(image_counts)
        application_list: list[ApplicationData] = [
            ApplicationData(**a) for a in json.loads(applications)
        ]
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("Invalid batch parameters: %s", exc)
        raise HTTPException(status_code=422, detail="Invalid batch parameters.") from exc

    if not counts or any(not isinstance(c, int) or c < 1 for c in counts):
        raise HTTPException(
            status_code=422,
            detail="image_counts must be a JSON array of positive integers.",
        )
    if any(c > MAX_IMAGES_PER_LABEL for c in counts):
        raise HTTPException(
            status_code=422,
            detail=f"Each image count must be <= {MAX_IMAGES_PER_LABEL}.",
        )

    if len(counts) != len(application_list):
        raise HTTPException(
            status_code=422,
            detail=(
                f"image_counts length ({len(counts)}) must match "
                f"applications length ({len(application_list)})."
            ),
        )
    if sum(counts) != len(files):
        raise HTTPException(
            status_code=422,
            detail="Sum of image_counts must match the number of uploaded files.",
        )

    daily_spend_guard.record_request(len(application_list))

    label_file_groups: list[list[UploadFile]] = []
    offset = 0
    for count in counts:
        label_file_groups.append(files[offset: offset + count])
        offset += count

    results: list[ReviewResult] = await asyncio.gather(
        *[_review_single(lf, ad) for lf, ad in zip(label_file_groups, application_list)]
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


@app.post("/api/review/batch/stream", dependencies=[Depends(require_demo_access)])
@limiter.limit(get_review_rate_limit)
async def review_labels_batch_stream(
    request: Request,
    files: list[UploadFile] = File(...),
    image_counts: str = Form(...),
    applications: str = Form(...),
) -> StreamingResponse:
    """Streaming batch review: yields NDJSON objects as each label review finishes.

    Pre-reads all file bytes upfront (concurrently) before streaming, then
    processes each label in sequence, immediately yielding:
      {"index": int, "result": ReviewResult}
    and concluding with:
      {"done": true, "total": int}
    """
    batch_start = time.monotonic()
    try:
        counts: list[int] = json.loads(image_counts)
        application_list: list[ApplicationData] = [
            ApplicationData(**a) for a in json.loads(applications)
        ]
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("Invalid batch parameters in stream: %s", exc)
        raise HTTPException(status_code=422, detail="Invalid batch parameters.") from exc

    if not counts or any(not isinstance(c, int) or c < 1 for c in counts):
        raise HTTPException(
            status_code=422,
            detail="image_counts must be a JSON array of positive integers.",
        )
    if any(c > MAX_IMAGES_PER_LABEL for c in counts):
        raise HTTPException(
            status_code=422,
            detail=f"Each image count must be <= {MAX_IMAGES_PER_LABEL}.",
        )

    if len(counts) != len(application_list):
        raise HTTPException(
            status_code=422,
            detail=(
                f"image_counts length ({len(counts)}) must match "
                f"applications length ({len(application_list)})."
            ),
        )
    if sum(counts) != len(files):
        raise HTTPException(
            status_code=422,
            detail="Sum of image_counts must match the number of uploaded files.",
        )

    daily_spend_guard.record_request(len(application_list))

    # Read all files concurrently before the response stream begins
    async def _read_file_entry(f: UploadFile) -> tuple[bytes, str]:
        content = await f.read()
        return content, f.filename or "unknown"

    all_file_data = await asyncio.gather(*[_read_file_entry(f) for f in files])

    file_data_groups: list[list[tuple[bytes, str]]] = []
    offset = 0
    for count in counts:
        file_data_groups.append(all_file_data[offset: offset + count])
        offset += count

    async def generate():
        total_claude_time_ms = 0
        successful_labels = 0
        for idx, (file_data, app_data) in enumerate(zip(file_data_groups, application_list)):
            start = time.monotonic()
            claude_call_start = 0
            claude_call_duration_ms = 0
            try:
                # Validate and prepare images in thread pool
                prepared_or_errors = await asyncio.gather(
                    *[
                        run_in_threadpool(
                            lambda c=content, fn=fname: (
                                prepare_image(c, fn),
                                _media_type_for(fn),
                            )
                            if _is_image_bytes(c) and len(c) <= MAX_FILE_SIZE and len(c) > 0
                            else (
                                None,
                                f"{fn} exceeds 10 MB limit." if len(c) > MAX_FILE_SIZE
                                else f"{fn} is empty." if len(c) == 0
                                else f"{fn} is not a supported image file."
                            )
                        )
                        for content, fname in file_data
                    ]
                )

                for prepared, err_or_mime in prepared_or_errors:
                    if prepared is None:
                        raise ValueError(err_or_mime)

                processed_images = [
                    (prep, fname)
                    for (prep, _), (_, fname) in zip(prepared_or_errors, file_data)
                ]

                claude_call_start = time.monotonic()
                extracted, extract_err = await _extract_fields_with_retry(processed_images)
                if extract_err or extracted is None:
                    raise RuntimeError(extract_err or "Extraction returned no data.")

                claude_call_duration_ms = int((time.monotonic() - claude_call_start) * 1000)
                total_claude_time_ms += claude_call_duration_ms

                # 27 CFR 16.22 Option A Scale Marker Type-Size Verification
                type_size_details: TypeSizeMeasurement | None = None
                if processed_images:
                    gw_locs = [loc for loc in extracted.field_locations if getattr(loc, "field", None) == "government_warning"]
                    gw_bbox = (gw_locs[0].x, gw_locs[0].y, gw_locs[0].width, gw_locs[0].height) if gw_locs else None
                    for img_bytes, img_name in processed_images:
                        try:
                            ts_res = await run_in_threadpool(
                                evaluate_type_size_from_image_bytes,
                                img_bytes,
                                filename=img_name,
                                net_contents=app_data.net_contents or extracted.net_contents,
                                gw_bbox=gw_bbox,
                            )
                        except Exception as exc:  # noqa: BLE001
                            _log.warning("Type-size evaluation error in stream for '%s': %s", img_name, exc)
                            ts_res = TypeSizeMeasurement(
                                method=CalibrationMethod.UNCALIBRATED_ESTIMATE,
                                required_min_height_mm=2.0,
                                state="cannot_measure",
                                verification_note=f"Type-size evaluation encountered an unexpected condition ({type(exc).__name__}). Physical gauge measurement recommended.",
                            )
                        if ts_res.method not in (CalibrationMethod.UNCALIBRATED_ESTIMATE, CalibrationMethod.NONE):
                            type_size_details = ts_res
                            break
                        if type_size_details is None:
                            type_size_details = ts_res

                fields = run_compliance_checks(app_data, extracted, type_size_details=type_size_details)
                status = overall_status(fields)
                wall_ms = int((time.monotonic() - start) * 1000)
                result = ReviewResult(
                    filenames=[fname for _, fname in processed_images],
                    overall_status=status,
                    fields=fields,
                    extracted=extracted,
                    processing_time_ms=wall_ms,
                )
                successful_labels += 1
                _log_request_timing(
                    endpoint="/api/review/batch/stream",
                    wall_time_ms=wall_ms,
                    claude_time_ms=claude_call_duration_ms,
                    claude_calls=1,
                    image_count=len(processed_images),
                    status=status,
                    extra={"batch_index": idx},
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
                # Hygiene: Do not echo raw unhandled exception strings to clients;
                # surface user-actionable error messages or a safe generic failure message.
                if isinstance(exc, ImageQualityError):
                    safe_error = exc.user_message
                elif isinstance(exc, ValueError):
                    safe_error = str(exc)
                elif isinstance(exc, AuthenticationError):
                    safe_error = "API key is invalid or missing. Check the ANTHROPIC_API_KEY environment variable."
                elif isinstance(exc, RateLimitError):
                    safe_error = "Anthropic rate limit reached. Please try again shortly."
                elif isinstance(exc, APITimeoutError):
                    safe_error = "The request to Claude timed out. Please try again."
                elif isinstance(exc, RuntimeError) and "busy" in str(exc).lower():
                    safe_error = str(exc)
                else:
                    safe_error = "An error occurred while reviewing this label. Please submit a new photo or try again."

                line = json.dumps({
                    "index": idx,
                    "result": {
                        "filenames": [fname for _, fname in file_data],
                        "overall_status": "fail",
                        "fields": [],
                        "extracted": {"government_warning_present": False, "field_locations": []},
                        "processing_time_ms": wall_ms,
                        "error": safe_error,
                    },
                })
            yield line + "\n"

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
    wall_time_ms = int((time.monotonic() - start) * 1000)

    if error_msg:
        _log_request_timing(
            endpoint="/api/label-check",
            wall_time_ms=wall_time_ms,
            claude_time_ms=metrics.claude_time_ms,
            claude_calls=metrics.claude_calls,
            image_count=len(files),
            status="error",
        )
        return LabelCheckResult(
            filenames=filenames,
            overall_status="fail",
            checks=[],
            extracted=ExtractedLabelData(),
            processing_time_ms=wall_time_ms,
            error=error_msg,
        )

    # 27 CFR 16.22 Option A Scale Marker Type-Size Verification
    type_size_details: TypeSizeMeasurement | None = None
    if metrics.images:
        gw_locs = [loc for loc in extracted.field_locations if getattr(loc, "field", None) == "government_warning"]
        gw_bbox = (gw_locs[0].x, gw_locs[0].y, gw_locs[0].width, gw_locs[0].height) if gw_locs else None
        for img_bytes, img_name in metrics.images:
            try:
                ts_res = await run_in_threadpool(
                    evaluate_type_size_from_image_bytes,
                    img_bytes,
                    filename=img_name,
                    net_contents=extracted.net_contents,
                    gw_bbox=gw_bbox,
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning("Type-size evaluation error in _label_check_single for '%s': %s", img_name, exc)
                ts_res = TypeSizeMeasurement(
                    method=CalibrationMethod.UNCALIBRATED_ESTIMATE,
                    required_min_height_mm=2.0,
                    state="cannot_measure",
                    verification_note=f"Type-size evaluation encountered an unexpected condition ({type(exc).__name__}). Physical gauge measurement recommended.",
                )
            if ts_res.method not in (CalibrationMethod.UNCALIBRATED_ESTIMATE, CalibrationMethod.NONE):
                type_size_details = ts_res
                break
            if type_size_details is None:
                type_size_details = ts_res

    type_confirmed = confirmed_beverage_type is not None
    try:
        checks = check_label_requirements(
            extracted,
            confirmed_beverage_type=confirmed_beverage_type,
            type_size_details=type_size_details,
        )
    except LowConfidenceError as exc:
        _log_request_timing(
            endpoint="/api/label-check",
            wall_time_ms=wall_time_ms,
            claude_time_ms=metrics.claude_time_ms,
            claude_calls=metrics.claude_calls,
            image_count=len(files),
            status="fail",
            extra={"error_type": "LowConfidenceError"},
        )
        return LabelCheckResult(
            filenames=filenames,
            overall_status="fail",
            checks=[],
            extracted=extracted,
            beverage_type=confirmed_beverage_type or extracted.beverage_type_guess,
            beverage_type_confirmed=type_confirmed,
            processing_time_ms=wall_time_ms,
            error=exc.user_message,
            photo_sources=effective_roles,
        )
    except ValueError as exc:
        if str(exc) == UNCONFIRMED_BEVERAGE_TYPE:
            _log_request_timing(
                endpoint="/api/label-check",
                wall_time_ms=wall_time_ms,
                claude_time_ms=metrics.claude_time_ms,
                claude_calls=metrics.claude_calls,
                image_count=len(files),
                status="warning",
                extra={"needs_confirmation": True},
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
            )
        raise

    status = overall_status(checks)
    _log_request_timing(
        endpoint="/api/label-check",
        wall_time_ms=wall_time_ms,
        claude_time_ms=metrics.claude_time_ms,
        claude_calls=metrics.claude_calls,
        image_count=len(files),
        status=status,
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
    )


@app.post("/api/label-check/batch", response_model=list[LabelCheckResult], dependencies=[Depends(require_demo_access)])
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
    try:
        counts: list[int] = json.loads(image_counts)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("Invalid image_counts: %s", exc)
        raise HTTPException(status_code=422, detail="Invalid image_counts.") from exc

    if not counts or any(not isinstance(c, int) or c < 1 for c in counts):
        raise HTTPException(
            status_code=422,
            detail="image_counts must be a JSON array of positive integers.",
        )
    if any(c > MAX_IMAGES_PER_LABEL for c in counts):
        raise HTTPException(
            status_code=422,
            detail=f"Each image count must be <= {MAX_IMAGES_PER_LABEL}.",
        )
    if (
        confirmed_beverage_type
        and confirmed_beverage_type not in _ALLOWED_BEVERAGE_TYPES
    ):
        raise HTTPException(
            status_code=422,
            detail="confirmed_beverage_type must be one of: distilled_spirits, wine, beer.",
        )

    roles_list: list[str] = []
    if photo_roles.strip():
        try:
            roles_list = json.loads(photo_roles)
        except (json.JSONDecodeError, ValueError) as exc:
            _log.warning("Invalid photo_roles: %s", exc)
            raise HTTPException(status_code=422, detail="Invalid photo_roles.") from exc
        if not isinstance(roles_list, list) or any(not isinstance(r, str) for r in roles_list):
            raise HTTPException(
                status_code=422,
                detail="photo_roles must be a JSON array of strings.",
            )

    if sum(counts) != len(files):
        raise HTTPException(
            status_code=422,
            detail="Sum of image_counts must match the number of uploaded files.",
        )

    daily_spend_guard.record_request(len(counts))

    label_file_groups: list[list[UploadFile]] = []
    label_role_groups: list[list[str]] = []
    offset = 0
    for count in counts:
        label_file_groups.append(files[offset: offset + count])
        label_role_groups.append(roles_list[offset: offset + count] if roles_list else [])
        offset += count

    bev_type = confirmed_beverage_type.strip() or None
    results: list[LabelCheckResult] = await asyncio.gather(
        *[
            _label_check_single(lf, confirmed_beverage_type=bev_type, photo_roles=roles)
            for lf, roles in zip(label_file_groups, label_role_groups)
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
