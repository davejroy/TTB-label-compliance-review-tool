# Handoff Document - TTB Label Compliance Review Tool (PRODUCTION)

> **Production repo**: [TTB-label-compliance-review-tool](https://github.com/davejroy/TTB-label-compliance-review-tool)
> **Development repo**: [TTB-label-compliance-review-tool-dev](https://github.com/davejroy/TTB-label-compliance-review-tool-dev)

---

This document summarizes the current state of the project for whoever picks
up work next: what the tool does, what's been built, what's in progress, and
where to find more detail.

## Current Status

This is a functional prototype operating in production. Both review modes (COLA Application Match and Label-Only Check) are implemented end-to-end with high compliance accuracy, streaming batch review, image highlight visualizer, and strict fail-open / zero-login access for evaluators.

## What Changed Recently

- **Fail-Open Security Remediation & Abuse Guardrails (September 2026)**:
  - **Soft Rate Limiting (High)**: Integrated `slowapi` inbound rate limiting on expensive review endpoints (`/api/review`, `/api/review/batch`, `/api/review/batch/stream`, `/api/label-check/batch`) keyed by client IP (`X-Forwarded-For` first hop or `request.client.host`). Returns HTTP **429** Too Many Requests with retry advice, NOT 401. Endpoints `/api/health` and `/api/demo-info` remain strictly unlimited.
  - **Concurrency Cap for Claude Calls**: Process-wide `asyncio.Semaphore` (configurable via `MAX_CONCURRENT_CLAUDE_CALLS`, default 5; timeout `CLAUDE_CONCURRENCY_TIMEOUT`, default 30.0s) wrapping Claude vision extractions across single, batch, and streaming endpoints to prevent unbounded-parallel burn of Anthropic API credits. Returns a friendly 503/429 busy message upon timeout — never an auth error.
  - **Spend / Abuse Soft Guardrail**: Implemented `DailySpendGuard` (`MAX_REVIEW_REQUESTS_PER_DAY`) tracking process-wide review volume and logging a loud warning when threshold is exceeded without blocking evaluator access. Documented that multi-instance deployments will require Redis in future iterations.
  - **Hygiene & Hardening**: Explicitly capped Pillow `Image.MAX_IMAGE_PIXELS = 64_000_000` to prevent decompression bombs. Masked raw exception strings in streaming batch responses (`/api/review/batch/stream`) with server-side logs and user-safe failure messages. Clarified CORS fail-open `*` default in docs and verified `DEMO_ACCESS_TOKEN` is left unset for public evaluator demos.
  - **Test Coverage**: Added test suite `backend/tests/test_rate_limit_and_concurrency.py` (8 new tests, 116 total pytest tests passing; 65 frontend vitest tests passing).

- **Accuracy, Fill Standards & Honesty Gaps Modernization (September 2026)**:
  - **Issue #9 P1 — Brand Name Containment Guard**: Implemented deterministic check (`_check_brand_name_match`, `_check_label_brand_name`, `_is_brand_in_address`, and `_has_distinct_brand_evidence` in `backend/app/compliance.py`) so brand names extracted or hallucinated from the producer/bottler address line cannot clean-pass without distinct brand heading evidence (such as spatial bounding box separation or extraction notes), returning "warning" (needs review).
  - **Issue #5 W02 — Missing Government Warning Structured Failure**: When the Government Warning is absent (`present=False` or empty), `_check_government_warning` and `assert_extraction_confidence` emit a structured `FieldResult(status="fail")` immediately instead of raising a `LowConfidenceError` photo retake prompt.
  - **Issue #6 W03 — Strict Government Warning Casing**: Any deviation from the canonical uppercase header `GOVERNMENT WARNING:` (such as `Government Warning:` or `government warning:`) hard-fails with `status="fail"` under 27 CFR 16.21/16.22 across all modes (COLA match, batch review, label-only check).
  - **Issue D — Treasury Decision TTB-200 Standards of Fill**: Updated `_WINE_FILL_ML`, `_WINE_FILL_FLOZ`, `_SPIRITS_FILL_ML`, and `_SPIRITS_FILL_FLOZ` in `backend/app/compliance.py` to incorporate all current authorized packaging sizes under 27 CFR 4.72 and 27 CFR 5.203 effective January 10, 2025 per T.D. TTB-200 (89 FR 96570). Malt beverages (Part 7) remain unrestricted.
  - **Issue E — 27 CFR 16.22 Honesty Advisory**: Added explicit advisory note to passing Government Warning verification results stating that physical millimeter type-size is not verified from uncalibrated photos and requires a physical gauge. Updated `InstructionsModal` Known Gaps & Scope copy to reflect that TTB-200 fill standards are automated while 16.22 physical type size requires physical measurement.
  - **Regression Testing**: Added full regression test coverage in `backend/tests/test_compliance.py` and `backend/tests/test_mode_parity_and_case.py`.

- **Safe Security Hardening (Zero-Login / Fail-Open)**:
  - Added centralized security response headers middleware (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, API `Content-Security-Policy: default-src 'none'`, and HSTS on HTTPS).
  - Sanitized error surfaces across Claude API and extraction runtime to prevent leaking internal exception details to clients.
  - Tightened batch input validation (`image_counts` positive integers cap ≤4, `photo_roles` array of strings, `confirmed_beverage_type` enum) returning HTTP 422 Unprocessable Entity.
  - Maintained fail-open CORS defaults (`*` when unset) allowing immediate evaluator and production frontend access.
  - Added comprehensive automated test suite `test_safe_hardening.py` covering anonymous access, security headers, safer error surfaces, and 422 batch validation.

## How to Run Locally

```bash
# Backend
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set ANTHROPIC_API_KEY
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev
```

## How to Test

```bash
# Backend tests
cd backend && pytest

# Frontend tests
cd frontend && npm test -- --run
```

## How to Build

```bash
cd frontend && npm run build
```

## How to Deploy

Deployed on Render.com via `render.yaml` Blueprint:
- `ttb-label-backend`: Python FastAPI service.
- `ttb-label-frontend`: Static site serving React dist.

## Required Environment Variables

| Variable | Purpose | Required | Example | Secret? |
|---|---|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude vision extraction | Yes | `sk-ant-...` | Yes |
| `CLAUDE_MODEL` | Claude model override | No | `claude-sonnet-4-6` | No |
| `CORS_ORIGINS` | Allowed CORS origins (defaults to `*` fail-open) | No | `https://ttb-label-frontend.onrender.com` | No |
| `DEMO_ACCESS_TOKEN` | Optional demo access gate token (defaults to open) | No | `secret-token` | Yes |
| `DEMO_USERNAME` | Display username in demo modal | No | `ttb-demo` | No |

## External Dependencies

- Anthropic Claude API (`anthropic` Python SDK) for label extraction and vision analysis.

## Known Issues

- Standards of fill check validates against modernized T.D. TTB-200 authorized size lists (§4.72 / §5.203); custom authorized sizes require manual formula verification.
- Exact physical type-size verification (27 CFR 16.22) is evaluated via OCR text match and confidence with honest advisory disclosure; physical millimeter measurement requires a physical gauge.

## Operational Notes

- Stateless request lifecycle; no persistent database or image storage (NFR-2).
- Structured latency logs emitted under `RequestTiming`.

## Security Notes

- **Fail-Open / Zero-Login Sacred Constraint**: Anonymous access is guaranteed; no 401/403 gates block evaluators on public API routes.
- Security response headers set on all requests via `setdefault`.
- Client-facing error messages are sanitized; stack traces logged only on backend.

## Next Recommended Actions

1. Integrate with COLA to pull application data directly.
2. Implement state-level requirement extensions.

## Open Questions

- None blocking for current release.

## Handoff Checklist
- [x] Code builds
- [x] Tests pass (all backend tests + 65 frontend tests pass)
- [x] Required docs updated (/docs/ERROR_CODES.md, HANDOFF.md, PDR.md, REGULATORY_REFRENCES.md, SBOM.md, TECHNICAL_ARCHITECTURE.md)
- [x] Secrets removed / verified no secrets committed
- [x] Dependencies reviewed
- [x] SBOM updated
- [x] Error codes updated
- [x] Architecture updated
- [x] Regulatory references updated

## What's implemented

- **Single Label review** - upload 1-4 label images plus COLA application
  data (brand name, class/type, ABV, net contents); Claude vision extracts
  the label fields and the backend compares them against the application
  data, returning Pass / Needs Review / Fail with explanations.
- **Batch Review & Streaming** - queue multiple label/application pairs, review them with progressive NDJSON streaming, see a summary table, and export results as CSV.
- **Label-Only Check** - validate a label against TTB mandatory requirements
  (27 CFR Parts 4, 5, 7, 16) without any application data: brand name,
  class/type, ABV statement (with correct requirement/exemption logic per
  beverage type), net contents, bottler/producer/importer name & address,
  country of origin (imports only), and the Government Warning statement.
- **Multi-Photo Merging & Sulfite Declaration Fix** - front and back label photos are extracted independently and merged by prioritizing non-empty field values over empty ones and arbitrating by highest-confidence score. Fixes sulfite declaration and government warning omissions when one panel has higher overall image quality but lacks the field.
- **In-App Instructions & Help Guide Modal** - header-accessible guide modal explaining tool workflows (COLA match vs Label-Only), photo capture best practices (front+back multi-photo merge), photo quality tips (lighting, flatness, glare prevention), review results meaning (Pass / Needs Review / Fail), low-confidence retake prompts vs hard regulatory fails, strict Government Warning casing (`GOVERNMENT WARNING:`), formula-dependent statement verification caveats (sulfites/allergens), and known gaps (27 CFR 16.22 type-size and T.D. TTB-200 fill standards). Fully accessible and zero-login.
- **Frontend Polish & Performance** - submit guards across all forms to prevent duplicate requests on rapid clicks, dynamic status copy during cold start or long processing, processing time display (`processing_time_ms`), and client-side image downscaling to ≤1600px with quality 0.92 before upload.
- **Image viewer** - zoomable label image viewer with per-field region
  highlighting and High/Medium/Low confidence badges based on Claude's
  transcription confidence.
- **Manual override** - agents can manually edit/override extracted text
  when OCR quality is poor.
- **Branding** - frontend uses TTB.gov-style branding, including the
  official TTB logo in the header and favicon.
- **Camera capture** - "Take Photo" option for capturing label images
  directly from a camera, shown only on touch-capable devices.

## Feature Flags & Production Defaults

- **`USE_FAST_EXTRACTION`**: **DEFAULT OFF** (`false`). In production, extraction always uses the standard Sonnet model (`claude-sonnet-4-6` or `claude-sonnet-4-5`). Fast extraction (`claude-3-5-haiku-latest`) is an optional feature flag for dev environments only and is never enabled by default in production or `render.yaml`.
- **Fail-Open / Zero-Login**: The app runs with open access by default when `DEMO_ACCESS_TOKEN` is unset, ensuring evaluators and agents are never locked out.

## Architecture quick reference

- **Backend:** Python / FastAPI (`backend/`), single Claude vision API call
  per review for label transcription, then rule-based compliance matching
  (no persistence of images or extracted data).
- **Frontend:** React + TypeScript + Vite + Tailwind CSS (`frontend/`).
- **Tests:** pytest covers backend compliance and model logic (`cd backend && pytest`), Vitest covers frontend (`cd frontend && npm test`).
- **Deploy:** see `render.yaml` for the Render.com deployment config.

## Recently implemented (previously "Known gaps")

The items below were listed as known gaps and have now been implemented:

- **Configurable tolerance rules per beverage class.** `BEVERAGE_TOLERANCE` in
  `compliance.py` maps each beverage class to its ABV tolerance and minimum
  extraction-confidence threshold.
- **Confidence-gated extraction.** `extraction_confidence` (0.0-1.0) is populated
  by Claude. Images below the per-class threshold raise `LowConfidenceError`
  (HTTP 422) and request a retake instead of silently passing.
- **Beverage-type confirmation.** `check_label_requirements` now accepts
  `confirmed_beverage_type` override; unknown types return
  `needs_beverage_confirmation=True` so the agent can confirm before evaluation.
- **Standards of fill validated.** Net contents now validated against 27 CFR
  4.72 (wine) / 5.203 (spirits) / 7.70 (beer) authorised size lists.
- **Formula-dependent caveats.** Sulfite, allergen, age-statement, and
  commodity-statement checks now carry `_FORMULA_DEPENDENT` notices.


### Phase 2 improvements (this session)

- **Per-field confidence thresholds** (`FIELD_CONFIDENCE_THRESHOLDS`): each field
  gets its own readability floor. Gov Warning body text allowed 0.45 vs brand name
  0.60 (long text on a curved bottle). Failed fields produce a named retake request.
- **Concurrent multi-photo extraction and merging** (`merge_extracted_label_data`;
  `photo_roles` batch param): front + back label photos extracted concurrently
  then merged by highest-confidence field value. Fixes spurious Gov Warning failures.
- **RequestTiming middleware**: logs request duration (`RequestTiming`) and returns `X-Process-Time` response header on all API endpoints.
- `LabelCheckResult.photo_sources` records which photo roles contributed.
- `ExtractedLabelData.per_field_confidence` dict in model, schema, and prompt.

### Phase 3 enhancements (this session)

- **Small-container alternate Government Warning text** (`SMALL_CONTAINER_THRESHOLD_ML = 100.0` in `compliance.py`): containers <= 100 mL accept either the full-form or the abbreviated body that omits clause numbers (1)/(2). Pass message notes which form was used. Per 27 CFR 16.21(c).
- **Beverage-type confirmation dialog** (`BeverageTypeDialog` in `LabelOnlyCheck.tsx`): when `needs_beverage_confirmation=True` the UI shows a modal with radio buttons for the three beverage types. Agent confirms; label is re-checked with `confirmed_beverage_type`; result updated in place without a full form re-submit.
- **Frontend types.ts updated**: `LabelCheckResult` now includes `needs_beverage_confirmation?`, `beverage_type_confirmed?`, `photo_sources?`.
- **`checkLabelsBatch` in `api.ts` updated**: accepts and passes `confirmedBeverageType` and `photoRoles` to the backend.

### W10 Missing-Brand & Anti-Hallucination Fix

- **Claude Client Prompt & Tool Schema Rules**: Explicit instruction in extraction tool schema and system prompt forbidding hallucinating, guessing, or substituting brand names from class/type designation, producer names, or artwork. If brand is missing or unreadable, return empty string `""` and assign `brand_name` score `0.0` (or `<0.35`) in `per_field_confidence`.
- **Compliance Empty-Brand Fail Enforcement**: `_check_text_field()` enforces that empty/missing brand names on label or application always result in a `fail` status (never `pass`).
- **Low-Confidence Retake Gate**: Low confidence scores for `brand_name` trigger `LowConfidenceError` with a retake recommendation rather than silently passing.
- **Regression Test Suite**: Added W10 regression tests in `backend/tests/test_compliance.py` verifying empty brand fail, empty app/label fail, whitespace fail, and low-confidence retake gate.

## Remaining next steps

- Integrate with COLA to pull application data automatically.
- Add state-level ABV and label requirement checks.
## Where to look first

- `README.md` - setup, running locally, feature overview.
- `docs/PDR.md` - full requirements, architecture, and design
  decisions/trade-offs.
- `docs/SBOM.md` - dependency inventory for backend and frontend.
