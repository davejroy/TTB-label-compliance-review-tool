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
from dataclasses import dataclass
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
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from .claude_client import extract_label_fields, prepare_image, _media_type_for, ImageQualityError
from .compliance import (
    LowConfidenceError,
    UNCONFIRMED_BEVERAGE_TYPE,
    check_label_requirements,
    merge_extracted_label_data,
    overall_status,
    run_compliance_checks,
)
from .models import ApplicationData, ExtractedLabelData, LabelCheckResult, ReviewResult
from .auth import require_demo_access, demo_username, auth_enabled

# Module-level logger. In production configure JSON handler for log aggregators.
_log = logging.getLogger(__name__)


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
    allow_methods=["GET", "POST", "OPTIONS"],
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


def _reject_if_not_image(data: bytes, filename: str) -> str | None:
    """Return a user-facing error if 'data' is not a recognized image, else None.

    WEBP ('RIFF'....'WEBP') is handled as a special case. HEIC/HEIF are not
    listed here and will be rejected with a clear message (Pillow cannot decode
    them without extra plugins); the frontend already advises re-taking as JPEG.
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return None
    if any(data.startswith(sig) for sig in _IMAGE_SIGNATURES):
        return None
    _log.info("Rejected non-image upload '%s' (unrecognized signature).", filename)
    return (
        f"File '{filename}' is not a supported image. Please upload a JPEG, "
        f"PNG, WEBP, GIF, BMP, or TIFF photo of the label."
    )


async def _read_and_validate_file(
    file: UploadFile,
) -> tuple[bytes, str] | str:
    """Read one uploaded file, validate it, and return pre-processed bytes.

    Returns ``(processed_jpeg_bytes, filename)`` on success, or an error
    string on failure. Using pre-processed bytes means extract_label_fields
    can skip the Pillow pipeline entirely (preprocessed=True), avoiding
    double-processing the same image.
    """
    image_bytes = await file.read()
    if len(image_bytes) > MAX_FILE_SIZE:
        return f"File '{file.filename}' exceeds 10 MB limit."
    sig_error = _reject_if_not_image(image_bytes, file.filename or "label.jpg")
    if sig_error:
        return sig_error
    try:
        processed_bytes, _media_type = await run_in_threadpool(
            prepare_image,
            image_bytes,
            file.filename or "label.jpg",
        )
    except ImageQualityError as exc:
        # Surface the user-friendly message directly - no internal details.
        # HTTP 422 Unprocessable Entity signals a client-fixable input problem
        # (per RFC 9110 §15.5.21), distinct from 400 (bad request structure).
        _log.info("Image quality rejected for '%s': %s", file.filename, exc.user_message)
        return exc.user_message
    except ValueError as exc:
        # Unexpected decode error - include filename but not internal exc detail.
        _log.warning("Decode error for '%s': %s", file.filename, exc)
        return f"File '{file.filename}' could not be read as a valid image. Please submit a new photo."
    return processed_bytes, file.filename or "label.jpg"


async def _read_images(
    files: list[UploadFile],
    filenames: list[str],
) -> list[tuple[bytes, str]] | str:
    """Read and validate all uploaded files concurrently.

    Returns a list of (processed_bytes, filename) tuples on success, or an
    error string describing the first file that failed validation.
    """
    # Enforce maximum-images-per-label limit before allocating any I/O resources.
    # Returning a plain string signals an error to callers (_extract_fields).
    if len(files) > MAX_IMAGES_PER_LABEL:
        _log.warning(
            "Upload rejected: %d files submitted, limit is %d.",
            len(files),
            MAX_IMAGES_PER_LABEL,
        )
        return (
            f"A maximum of {MAX_IMAGES_PER_LABEL} images may be uploaded per label. "
            f"You submitted {len(files)}. Please resubmit with 4 or fewer photos."
        )
    read_results = await asyncio.gather(*[_read_and_validate_file(f) for f in files])
    images: list[tuple[bytes, str]] = []
    for result in read_results:
        if isinstance(result, str):
            return result
        images.append(result)
    return images


@dataclass
class ExtractionMetrics:
    """Holds timing and call counts for an extraction operation."""
    claude_time_ms: int = 0
    claude_calls: int = 0


async def _extract_fields_with_retry(
    images: list[tuple[bytes, str]],
    metrics: ExtractionMetrics | None = None,
) -> tuple[ExtractedLabelData | None, str | None]:
    """Call extract_label_fields with one automatic retry on transient errors.

    Images are passed as pre-processed JPEG bytes (preprocessed=True) so
    Claude client skips the Pillow pipeline entirely.

    Returns ``(extracted, None)`` on success or ``(None, error_msg)`` on
    failure after exhausting retries.
    """
    for attempt in range(2):
        call_start = time.monotonic()
        try:
            if metrics is not None:
                metrics.claude_calls += 1
            extracted = await run_in_threadpool(
                extract_label_fields, images, True
            )
            if metrics is not None:
                metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
            return extracted, None
        except AuthenticationError:
            if metrics is not None:
                metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
            return None, "API key is invalid or missing. Check the ANTHROPIC_API_KEY environment variable."
        except _RETRYABLE as exc:
            if metrics is not None:
                metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
            if attempt == 0:
                # First attempt failed on a transient error - wait then retry.
                await asyncio.sleep(_RETRY_DELAY_S)
                continue
            # Second attempt also failed.
            if isinstance(exc, RateLimitError):
                return None, "Anthropic rate limit reached. Please try again shortly."
            if isinstance(exc, APITimeoutError):
                return None, "The request to Claude timed out. Please try again."
            _log.warning("Claude connection error after retry: %s", exc)
            return None, "Network error contacting Claude API. Please try again shortly."
        except APIStatusError as exc:
            if metrics is not None:
                metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
            # 529 = overloaded; retry once.
            if attempt == 0 and exc.status_code == 529:
                await asyncio.sleep(_RETRY_DELAY_S)
                continue
            return None, f"Claude API error ({exc.status_code}): {exc.message}"
        except ValueError as exc:
            if metrics is not None:
                metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
            # Non-alcohol label guard (raised by extract_label_fields when
            # is_alcohol_beverage_label is False). Surface directly to user.
            return None, str(exc)
        except RuntimeError as exc:
            if metrics is not None:
                metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
            _log.warning("Runtime extraction error: %s", exc)
            return None, "Could not process label image(s). Please submit clearer photos."
        except Exception as exc:  # noqa: BLE001
            if metrics is not None:
                metrics.claude_time_ms += int((time.monotonic() - call_start) * 1000)
            _log.exception("Unexpected extraction error")
            return None, "Unexpected error during label extraction. Please try again."
    return None, "Unexpected retry loop exit."



async def _extract_fields(
    files: list[UploadFile],
    filenames: list[str],
    photo_roles: list[str] | None = None,
    metrics: ExtractionMetrics | None = None,
) -> tuple[ExtractedLabelData | None, str | None, list[str]]:
    """Shared image-read + Claude-extraction pipeline with multi-photo merging.

    When photo_roles identifies multiple distinct panels (e.g. "front" and "back"),
    each photo is extracted concurrently via asyncio.gather and merged via
    merge_extracted_label_data.

    Returns (merged_extraction, error_msg, effective_roles).
    """
    if not (1 <= len(files) <= MAX_IMAGES_PER_LABEL):
        return None, f"Upload between 1 and {MAX_IMAGES_PER_LABEL} images per label.", []

    images_or_error = await _read_images(files, filenames)
    if isinstance(images_or_error, str):
        return None, images_or_error, []

    images: list[tuple[bytes, str]] = images_or_error
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

    extractions: list[ExtractedLabelData] = []
    for (ext, err), role in zip(gather_results, effective_roles):
        if err or ext is None:
            return None, f"Extraction failed for {role} photo: {err}", effective_roles
        extractions.append(ext)

    merged = await run_in_threadpool(merge_extracted_label_data, extractions, effective_roles)
    return merged, None, effective_roles

async def _review_single(files: list[UploadFile], application: ApplicationData) -> ReviewResult:
    """Run an application-vs-label review for one label's image(s)."""
    start = time.monotonic()
    filenames = [f.filename or "unknown" for f in files]
    metrics = ExtractionMetrics()

    extracted, error_msg, _roles = await _extract_fields(files, filenames, metrics=metrics)
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

    fields = run_compliance_checks(application, extracted)
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
async def review_label(
    files: list[UploadFile] = File(...),
    application: str = Form(...),
) -> ReviewResult:
    """Single-label review: 1-4 label images plus one JSON-encoded
    ApplicationData form field."""
    try:
        application_data = ApplicationData(**json.loads(application))
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("Invalid application data: %s", exc)
        raise HTTPException(status_code=422, detail="Invalid application data.") from exc
    return await _review_single(files, application_data)


@app.post("/api/review/batch", response_model=list[ReviewResult], dependencies=[Depends(require_demo_access)])
async def review_labels_batch(
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
async def review_labels_batch_stream(
    files: list[UploadFile] = File(...),
    image_counts: str = Form(...),
    applications: str = Form(...),
) -> StreamingResponse:
    """Streaming batch review — yields NDJSON result lines as each label completes.

    This endpoint processes labels sequentially and streams each result as a
    newline-terminated JSON object the moment it is ready, rather than waiting
    for all labels to finish. Clients can begin rendering results immediately,
    dramatically improving perceived performance for large batches.

    Response format: Content-Type: application/x-ndjson
    Each line is a complete JSON object with either a ReviewResult payload or
    an error object: {"index": N, "result": {...}} or {"index": N, "error": "..."}

    A sentinel line {"done": true, "total": N} is emitted after all results.
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

    label_file_groups: list[list[UploadFile]] = []
    offset = 0
    for count in counts:
        label_file_groups.append(files[offset: offset + count])
        offset += count

    # Read all file bytes eagerly before entering the async generator.
    # UploadFile objects can only be read once; we must do it before streaming
    # begins so the generator can operate without awaiting on request state.
    file_data_groups: list[list[tuple[bytes, str]]] = []
    for group in label_file_groups:
        group_data = []
        for f in group:
            raw = await f.read()
            group_data.append((raw, f.filename or "label.jpg"))
        file_data_groups.append(group_data)

    async def generate():
        total_claude_time_ms = 0
        successful_labels = 0
        for idx, (file_data, app_data) in enumerate(zip(file_data_groups, application_list)):
            start = time.monotonic()
            claude_call_start = 0
            claude_call_duration_ms = 0
            try:
                # Build UploadFile-compatible tuples for _review_single by
                # re-wrapping bytes. Since _review_single reads from UploadFile
                # objects, we use the lower-level helpers directly here.
                processed_images: list[tuple[bytes, str]] = []
                for raw_bytes, fname in file_data:
                    try:
                        proc_bytes, _ = await run_in_threadpool(prepare_image, raw_bytes, fname)
                        processed_images.append((proc_bytes, fname))
                    except ImageQualityError as exc:
                        raise ValueError(exc.user_message) from exc

                claude_call_start = time.monotonic()
                extracted = await run_in_threadpool(
                    extract_label_fields,
                    processed_images,
                    True,  # preprocessed=True
                )
                claude_call_duration_ms = int((time.monotonic() - claude_call_start) * 1000)
                total_claude_time_ms += claude_call_duration_ms
                fields = run_compliance_checks(app_data, extracted)
                status = overall_status([f.status for f in fields])
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
                line = json.dumps({
                    "index": idx,
                    "result": {
                        "filenames": [fname for _, fname in file_data],
                        "overall_status": "fail",
                        "fields": [],
                        "extracted": {"government_warning_present": False, "field_locations": []},
                        "processing_time_ms": wall_ms,
                        "error": str(exc),
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

    type_confirmed = confirmed_beverage_type is not None
    try:
        checks = check_label_requirements(
            extracted,
            confirmed_beverage_type=confirmed_beverage_type,
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
async def label_check_batch(
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
        if not isinstance(roles_list, list) or any(
            not isinstance(role, str) for role in roles_list
        ):
            raise HTTPException(
                status_code=422,
                detail="photo_roles must be a JSON array of strings.",
            )

    if sum(counts) != len(files):
        raise HTTPException(
            status_code=422,
            detail="Sum of image_counts must match the number of uploaded files.",
        )

    label_file_groups: list[list[UploadFile]] = []
    label_role_groups: list[list[str] | None] = []
    offset = 0
    for count in counts:
        label_file_groups.append(files[offset: offset + count])
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
