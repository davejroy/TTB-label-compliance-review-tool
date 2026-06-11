import json
import time

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .claude_client import extract_label_fields
from .compliance import overall_status, run_compliance_checks
from .models import ApplicationData, ExtractedLabelData, ReviewResult

app = FastAPI(title="TTB Label Compliance Review Tool")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


async def _review_single(file: UploadFile, application: ApplicationData) -> ReviewResult:
    start = time.monotonic()
    image_bytes = await file.read()

    if len(image_bytes) > MAX_FILE_SIZE:
        return ReviewResult(
            filename=file.filename or "unknown",
            overall_status="fail",
            fields=[],
            extracted=ExtractedLabelData(),
            processing_time_ms=0,
            error="File exceeds 10 MB limit.",
        )

    try:
        extracted = extract_label_fields(image_bytes, file.filename or "label.jpg")
    except Exception as exc:  # noqa: BLE001
        return ReviewResult(
            filename=file.filename or "unknown",
            overall_status="fail",
            fields=[],
            extracted=ExtractedLabelData(),
            processing_time_ms=int((time.monotonic() - start) * 1000),
            error=f"Could not read label image: {exc}",
        )

    fields = run_compliance_checks(application, extracted)

    return ReviewResult(
        filename=file.filename or "unknown",
        overall_status=overall_status(fields),
        fields=fields,
        extracted=extracted,
        processing_time_ms=int((time.monotonic() - start) * 1000),
    )


@app.post("/api/review", response_model=ReviewResult)
async def review_label(
    file: UploadFile = File(...),
    application: str = Form(...),
) -> ReviewResult:
    try:
        application_data = ApplicationData(**json.loads(application))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid application data: {exc}") from exc

    return await _review_single(file, application_data)


@app.post("/api/review/batch", response_model=list[ReviewResult])
async def review_labels_batch(
    files: list[UploadFile] = File(...),
    applications: str = Form(...),
) -> list[ReviewResult]:
    try:
        raw_applications = json.loads(applications)
        application_list = [ApplicationData(**item) for item in raw_applications]
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid application data: {exc}") from exc

    if len(files) != len(application_list):
        raise HTTPException(
            status_code=400,
            detail="Number of files must match number of application entries.",
        )

    results = []
    for file, application_data in zip(files, application_list):
        results.append(await _review_single(file, application_data))

    return results
