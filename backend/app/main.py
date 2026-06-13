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
- Batch requests run all per-label reviews concurrently (each is an
  independent Claude API call, so there is no ordering dependency).
- CPU-bound Pillow work (image downscaling) is offloaded to a thread pool via
  run_in_threadpool so it does not block the async event loop.
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

from .claude_client import _downscale_if_needed, _media_type_for, extract_label_fields
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


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


async def _read_and_validate_file(
    file: UploadFile,
) -> tuple[bytes, str] | str:
    """Read one uploaded file and return (bytes, filename) or an error string."""
    image_bytes = await file.read()
    if len(image_bytes) > MAX_FILE_SIZE:
        return f"File '{file.filename}' exceeds 10 MB limit."
    # Offload CPU-bound downscaling to a thread pool so the event loop stays free.
    resized_bytes, _media_type = await run_in_threadpool(
        _downscale_if_needed,
        image_bytes,
        _media_type_for(file.filename or "label.jpg"),
    )
    return resized_bytes, file.filename or "label.jpg"


async def _review_single(files: list[UploadFile], application: ApplicationData) -> ReviewResult:
    """Run an application-vs-label review for one label's image(s).

    Validates the upload (image count and per-file size limits), reads all
    files concurrently, then extracts fields via Claude and runs
    run_compliance_checks. Any failure along the way is returned as a
    ReviewResult with error set rather than raising, so a batch
    request can report per-item errors without failing the whole batch.
    """
    start = time.monotonic()
    filenames = [f.filename or "unknown" for f in files]

    if not (1 <= len(files) <= MAX_IMAGES_PER_LABEL):
        return ReviewResult(
            filenames=filenames,
            overall_status="fail",
            fields=[],
            extracted=ExtractedLabelData(),
            processing_time_ms=0,
            error=f"Upload between 1 and {MAX_IMAGES_PER_LABEL} images per label.",
        )

    # Read and validate all files concurrently.
    read_results = await asyncio.gather(*[_read_and_validate_file(f) for f in files])

    images: list[tuple[bytes, str]] = []
    for result in read_results:
        if isinstance(result, str):
            # Error message from _read_and_validate_file
            return ReviewResult(
                filenames=filenames,
                overall_status="fail",
                fields=[],
                extracted=ExtractedLabelData(),
                processing_time_ms=0,
                error=result,
            )
        images.append(result)

    error_msg: str | None = None
    try:
        extracted = extract_label_fields(images)
    except AuthenticationError:
        error_msg = "API key is invalid or missing. Check the ANTHROPIC_API_KEY environment variable."
    except RateLimitError:
        error_msg = "Anthropic rate limit reached. Please try again shortly."
    except APITimeoutError:
        error_msg = "The request to Claude timed out. Please try again."
    except APIConnectionError as exc:
        error_msg = f"Network error contacting Claude API: {exc}"
    except APIStatusError as exc:
        error_msg = f"Claude API error ({exc.status_code}): {exc.message}"
    except RuntimeError as exc:
        error_msg = f"Could not read label image(s): {exc}"
    except Exception as exc:  # noqa: BLE001
        error_msg = f"Unexpected error during label extraction: {exc}"

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
    ApplicationData form field (the "Single Review" mode)."""
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
    """Batch review: all labels' images concatenated in files, with
    image_counts (JSON array of ints) indicating how many files belong
    to each label, and applications (JSON array of ApplicationData)."""
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

    # Split the flat file list into per-label groups.
    label_file_groups: list[list[UploadFile]] = []
    offset = 0
    for count in counts:
        label_file_groups.append(files[offset : offset + count])
        offset += count

    # Run all per-label reviews concurrently - each is an independent Claude
    # API call, so there is no ordering dependency.
    results: list[ReviewResult] = await asyncio.gather(
        *[
            _review_single(label_files, app_data)
            for label_files, app_data in zip(label_file_groups, application_list)
        ]
    )
    return list(results)


async def _label_check_single(files: list[UploadFile]) -> LabelCheckResult:
    """Run a label-only requirements check for one label's image(s)."""
    start = time.monotonic()
    filenames = [f.filename or "unknown" for f in files]

    if not (1 <= len(files) <= MAX_IMAGES_PER_LABEL):
        return LabelCheckResult(
            filenames=filenames,
            overall_status="fail",
            checks=[],
            extracted=ExtractedLabelData(),
            processing_time_ms=0,
            error=f"Upload between 1 and {MAX_IMAGES_PER_LABEL} images per label.",
        )

    read_results = await asyncio.gather(*[_read_and_validate_file(f) for f in files])

    images: list[tuple[bytes, str]] = []
    for result in read_results:
        if isinstance(result, str):
            return LabelCheckResult(
                filenames=filenames,
                overall_status="fail",
                checks=[],
                extracted=ExtractedLabelData(),
                processing_time_ms=0,
                error=result,
            )
        images.append(result)

    error_msg: str | None = None
    try:
        extracted = extract_label_fields(images)
    except AuthenticationError:
        error_msg = "API key is invalid or missing. Check the ANTHROPIC_API_KEY environment variable."
    except RateLimitError:
        error_msg = "Anthropic rate limit reached. Please try again shortly."
    except APITimeoutError:
        error_msg = "The request to Claude timed out. Please try again."
    except APIConnectionError as exc:
        error_msg = f"Network error contacting Claude API: {exc}"
    except APIStatusError as exc:
        error_msg = f"Claude API error ({exc.status_code}): {exc.message}"
    except RuntimeError as exc:
        error_msg = f"Could not read label image(s): {exc}"
    except Exception as exc:  # noqa: BLE001
        error_msg = f"Unexpected error during label extraction: {exc}"

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
    """Label-Only Check (batch): all labels' images concatenated in files,
    with image_counts (JSON array of ints) indicating how many files belong
    to each label. No application data is required."""
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
        label_file_groups.append(files[offset : offset + count])
        offset += count

    results: list[LabelCheckResult] = await asyncio.gather(
        *[_label_check_single(label_files) for label_files in label_file_groups]
    )
    return list(results)
