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
- CPU-bound Pillow work is offloaded to a thread pool via run_in_threadpool.
- Images are prepared (validated, enhanced, downscaled) once in _read_and_validate_file
  and the processed bytes are passed directly to extract_label_fields with
  preprocessed=True, avoiding a second Pillow pass.
- Transient Claude API errors (rate-limit, overloaded, timeout) are retried
  once after a short delay before returning an error to the client.
"""

import asyncio
import json
import os
import time

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from .claude_client import extract_label_fields, prepare_image, _media_type_for
from .compliance import check_label_requirements, overall_status, run_compliance_checks
from .models import ApplicationData, ExtractedLabelData, LabelCheckResult, ReviewResult

app = FastAPI(title="TTB Label Compliance Review Tool")

# CORS - in production lock this down to the frontend's Render hostname via
# the CORS_ORIGINS environment variable (comma-separated). Defaults to "*"
# so local dev works without extra config.
_cors_origins = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB per image
MAX_IMAGES_PER_LABEL = 4

# Retry config for transient Claude API errors.
# One retry after a short delay handles most rate-limit / 529 overloaded blips.
_RETRY_DELAY_S = 2.0
_RETRYABLE = (RateLimitError, APITimeoutError, APIConnectionError)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


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
    try:
        processed_bytes, _media_type = await run_in_threadpool(
            prepare_image,
            image_bytes,
            file.filename or "label.jpg",
        )
    except ValueError as exc:
        return f"File '{file.filename}' could not be read as an image: {exc}"
    return processed_bytes, file.filename or "label.jpg"


async def _read_images(
    files: list[UploadFile],
    filenames: list[str],
) -> list[tuple[bytes, str]] | str:
    """Read and validate all uploaded files concurrently.

    Returns a list of (processed_bytes, filename) tuples on success, or an
    error string describing the first file that failed validation.
    """
    read_results = await asyncio.gather(*[_read_and_validate_file(f) for f in files])
    images: list[tuple[bytes, str]] = []
    for result in read_results:
        if isinstance(result, str):
            return result
        images.append(result)
    return images


async def _extract_fields_with_retry(
    images: list[tuple[bytes, str]],
) -> tuple[ExtractedLabelData | None, str | None]:
    """Call extract_label_fields with one automatic retry on transient errors.

    Images are passed as pre-processed JPEG bytes (preprocessed=True) so
    Claude client skips the Pillow pipeline entirely.

    Returns ``(extracted, None)`` on success or ``(None, error_msg)`` on
    failure after exhausting retries.
    """
    for attempt in range(2):
        try:
            extracted = await run_in_threadpool(
                extract_label_fields, images, True
            )
            return extracted, None
        except AuthenticationError:
            return None, "API key is invalid or missing. Check the ANTHROPIC_API_KEY environment variable."
        except _RETRYABLE as exc:
            if attempt == 0:
                # First attempt failed on a transient error - wait then retry.
                await asyncio.sleep(_RETRY_DELAY_S)
                continue
            # Second attempt also failed.
            if isinstance(exc, RateLimitError):
                return None, "Anthropic rate limit reached. Please try again shortly."
            if isinstance(exc, APITimeoutError):
                return None, "The request to Claude timed out. Please try again."
            return None, f"Network error contacting Claude API: {exc}"
        except APIStatusError as exc:
            # 529 = overloaded; retry once.
            if attempt == 0 and exc.status_code == 529:
                await asyncio.sleep(_RETRY_DELAY_S)
                continue
            return None, f"Claude API error ({exc.status_code}): {exc.message}"
        except RuntimeError as exc:
            return None, f"Could not read label image(s): {exc}"
        except Exception as exc:  # noqa: BLE001
            return None, f"Unexpected error during label extraction: {exc}"
    return None, "Unexpected retry loop exit."


async def _extract_fields(
    files: list[UploadFile],
    filenames: list[str],
) -> tuple[ExtractedLabelData | None, str | None]:
    """Shared image-read + Claude-extraction pipeline.

    Reads and pre-processes all images, then calls Claude once with the
    pre-processed bytes (no double Pillow pass). Retries once on transient
    API errors. Returns ``(extracted, None)`` or ``(None, error_msg)``.
    """
    if not (1 <= len(files) <= MAX_IMAGES_PER_LABEL):
        return None, f"Upload between 1 and {MAX_IMAGES_PER_LABEL} images per label."

    images_or_error = await _read_images(files, filenames)
    if isinstance(images_or_error, str):
        return None, images_or_error

    return await _extract_fields_with_retry(images_or_error)


async def _review_single(files: list[UploadFile], application: ApplicationData) -> ReviewResult:
    """Run an application-vs-label review for one label's image(s)."""
    start = time.monotonic()
    filenames = [f.filename or "unknown" for f in files]

    extracted, error_msg = await _extract_fields(files, filenames)

    if error_msg:
        return ReviewResult(
            filenames=filenames,
            overall_status="fail",
            fields=[],
            extracted=ExtractedLabelData(),
            processing_time_ms=int((time.monotonic() - start) * 1000),
            error=error_msg,
        )

    fields = run_compliance_checks(application, extracted)
    return ReviewResult(
        filenames=filenames,
        overall_status=overall_status(fields),
        fields=fields,
        extracted=extracted,
        processing_time_ms=int((time.monotonic() - start) * 1000),
    )


@app.post("/api/review", response_model=ReviewResult)
async def review_label(
    files: list[UploadFile] = File(...),
    application: str = Form(...),
) -> ReviewResult:
    """Single-label review: 1-4 label images plus one JSON-encoded
    ApplicationData form field."""
    try:
        application_data = ApplicationData(**json.loads(application))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid application data: {exc}") from exc
    return await _review_single(files, application_data)


@app.post("/api/review/batch", response_model=list[ReviewResult])
async def review_labels_batch(
    files: list[UploadFile] = File(...),
    image_counts: str = Form(...),
    applications: str = Form(...),
) -> list[ReviewResult]:
    """Batch review: all labels' images concatenated in files."""
    try:
        counts: list[int] = json.loads(image_counts)
        application_list: list[ApplicationData] = [
            ApplicationData(**a) for a in json.loads(applications)
        ]
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid batch parameters: {exc}") from exc

    if len(counts) != len(application_list):
        raise HTTPException(
            status_code=400,
            detail=(
                f"image_counts length ({len(counts)}) must match "
                f"applications length ({len(application_list)})."
            ),
        )
    if sum(counts) != len(files):
        raise HTTPException(
            status_code=400,
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
    return list(results)


async def _label_check_single(files: list[UploadFile]) -> LabelCheckResult:
    """Run a label-only requirements check for one label's image(s)."""
    start = time.monotonic()
    filenames = [f.filename or "unknown" for f in files]

    extracted, error_msg = await _extract_fields(files, filenames)

    if error_msg:
        return LabelCheckResult(
            filenames=filenames,
            overall_status="fail",
            checks=[],
            extracted=ExtractedLabelData(),
            processing_time_ms=int((time.monotonic() - start) * 1000),
            error=error_msg,
        )

    checks = check_label_requirements(extracted, extracted.beverage_type_guess)
    return LabelCheckResult(
        filenames=filenames,
        overall_status=overall_status(checks),
        beverage_type=extracted.beverage_type_guess,
        checks=checks,
        extracted=extracted,
        processing_time_ms=int((time.monotonic() - start) * 1000),
    )


@app.post("/api/label-check/batch", response_model=list[LabelCheckResult])
async def label_check_batch(
    files: list[UploadFile] = File(...),
    image_counts: str = Form(...),
) -> list[LabelCheckResult]:
    """Label-Only Check (batch): all labels' images concatenated in files."""
    try:
        counts: list[int] = json.loads(image_counts)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image_counts: {exc}") from exc

    if sum(counts) != len(files):
        raise HTTPException(
            status_code=400,
            detail="Sum of image_counts must match the number of uploaded files.",
        )

    label_file_groups: list[list[UploadFile]] = []
    offset = 0
    for count in counts:
        label_file_groups.append(files[offset: offset + count])
        offset += count

    results: list[LabelCheckResult] = await asyncio.gather(
        *[_label_check_single(lf) for lf in label_file_groups]
    )
    return list(results)
