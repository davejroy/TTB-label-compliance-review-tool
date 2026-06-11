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
"""

import json
import time

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .claude_client import extract_label_fields
from .compliance import check_label_requirements, overall_status, run_compliance_checks
from .models import ApplicationData, ExtractedLabelData, LabelCheckResult, ReviewResult

app = FastAPI(title="TTB Label Compliance Review Tool")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB per image
MAX_IMAGES_PER_LABEL = 4


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


async def _review_single(files: list[UploadFile], application: ApplicationData) -> ReviewResult:
    """Run an application-vs-label review for one label's image(s).

    Validates the upload (image count and per-file size limits), extracts
    fields via Claude, then runs ``run_compliance_checks``. Any failure
    along the way is returned as a ``ReviewResult`` with ``error`` set
    rather than raising, so a batch request can report per-item errors
    without failing the whole batch.
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

    images: list[tuple[bytes, str]] = []
    for file in files:
        image_bytes = await file.read()
        if len(image_bytes) > MAX_FILE_SIZE:
            return ReviewResult(
                filenames=filenames,
                overall_status="fail",
                fields=[],
                extracted=ExtractedLabelData(),
                processing_time_ms=0,
                error=f"File '{file.filename}' exceeds 10 MB limit.",
            )
        images.append((image_bytes, file.filename or "label.jpg"))

    try:
        extracted = extract_label_fields(images)
    except Exception as exc:  # noqa: BLE001
        return ReviewResult(
            filenames=filenames,
            overall_status="fail",
            fields=[],
            extracted=ExtractedLabelData(),
            processing_time_ms=int((time.monotonic() - start) * 1000),
            error=f"Could not read label image(s): {exc}",
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
    ``ApplicationData`` form field (the "Single Review" mode)."""
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
    """Batch review: all labels' images concatenated into ``files``, with
    ``image_counts`` (a JSON array of per-label image counts) and
    ``applications`` (a JSON array of per-label ``ApplicationData``)
    indicating how to split them back into per-label groups (the "Batch
    Review" mode)."""
    try:
        raw_applications = json.loads(applications)
        application_list = [ApplicationData(**item) for item in raw_applications]
        counts = json.loads(image_counts)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid application data: {exc}") from exc

    if len(counts) != len(application_list):
        raise HTTPException(
            status_code=400,
            detail="Number of image_counts entries must match number of application entries.",
        )

    if sum(counts) != len(files):
        raise HTTPException(
            status_code=400,
            detail="Sum of image_counts must match the number of uploaded files.",
        )

    results = []
    offset = 0
    for count, application_data in zip(counts, application_list):
        item_files = files[offset : offset + count]
        offset += count
        results.append(await _review_single(item_files, application_data))

    return results


async def _label_check_single(files: list[UploadFile]) -> LabelCheckResult:
    """Run a label-only requirements check for one label's image(s).

    Same upload validation/extraction flow as ``_review_single``, but runs
    ``check_label_requirements`` against ``extracted.beverage_type_guess``
    instead of comparing to application data.
    """
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

    images: list[tuple[bytes, str]] = []
    for file in files:
        image_bytes = await file.read()
        if len(image_bytes) > MAX_FILE_SIZE:
            return LabelCheckResult(
                filenames=filenames,
                overall_status="fail",
                checks=[],
                extracted=ExtractedLabelData(),
                processing_time_ms=0,
                error=f"File '{file.filename}' exceeds 10 MB limit.",
            )
        images.append((image_bytes, file.filename or "label.jpg"))

    try:
        extracted = extract_label_fields(images)
    except Exception as exc:  # noqa: BLE001
        return LabelCheckResult(
            filenames=filenames,
            overall_status="fail",
            checks=[],
            extracted=ExtractedLabelData(),
            processing_time_ms=int((time.monotonic() - start) * 1000),
            error=f"Could not read label image(s): {exc}",
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
    """Label-Only Check (batch): all labels' images concatenated into
    ``files``, with ``image_counts`` (a JSON array of per-label image
    counts) indicating how to split them. No application data is required."""
    try:
        counts = json.loads(image_counts)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image_counts: {exc}") from exc

    if sum(counts) != len(files):
        raise HTTPException(
            status_code=400,
            detail="Sum of image_counts must match the number of uploaded files.",
        )

    results = []
    offset = 0
    for count in counts:
        item_files = files[offset : offset + count]
        offset += count
        results.append(await _label_check_single(item_files))

    return results
