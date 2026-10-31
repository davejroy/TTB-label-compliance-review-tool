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

- **Re-promote to `-dev` `ceb18a8` (security PRs #50 / #51, October 2026)**:
  - Backend pins from `#50` `35a2189` + `#51` `ceb18a8`: `Pillow==13.0.0`, `python-dotenv==1.2.4`, `fastapi==0.162.0`, `starlette==1.10.0`.
  - Frontend pins from `#51`: `vite@8.3.5`, `vitest@5.0.5`, `happy-dom@21.0.4`, `zod@4.6.7` override. Production layout has no Gradio/Streamlit demo trees, so those `#50` demo pins were skipped.
  - Access-token gate stays wired. No secrets committed.
  - GitHub CI on the pin commit failed two jobs: the type-size `image_index` test raced `asyncio.gather` + threadpool call order (production pairing was already correct), and `tsc -b` typechecked Vitest files after Vitest 5 matcher types changed. Production typecheck now excludes `src/__tests__` and `src/test`.

- **PR #26 review-thread hardening (October 2026)**:
  - **Access-token gate stays ON**: `require_demo_access` remains a dependency on `/api/review`, `/api/review/batch`, `/api/review/batch/stream`, `/api/label-check/batch`, and `/api/cola/parse-template`. `/api/health` stays ungated. The gate still **fails open when `DEMO_ACCESS_TOKEN` is unset** (do not commit a token). Do not remove the dependency.
  - **Request size**: `RequestBodyLimitMiddleware` counts streamed / chunked ASGI body bytes as well as `Content-Length`. Oversize requests return HTTP 413 `FILE_TOO_LARGE`. `/api/health` is skipped.
  - **CORS on 413**: middleware order is body-limit → security headers → CORS (CORS outermost) so browsers still see `Access-Control-Allow-Origin` on short-circuit 413s.
  - **Docs CSP**: `/docs` and `/redoc` use a Swagger-CDN-capable CSP; JSON API routes keep `default-src 'none'`.
  - **Type-size bbox**: Government Warning boxes are applied only to the matching `field_locations.image_index`. Multi-photo merge remaps each extraction's locations to the merged photo index.
  - **Address parse**: trailing `CO` after a city (`Denver CO`) is Colorado, not the legal suffix `Co.`; `Brewing Co` remains a company name.
  - **Stream retry**: `/api/review/batch/stream` uses the same `_extract_fields_with_retry` path as sync/batch (semaphore + transient timeout retry).
  - **Bold heading**: `header_bold` is advisory only (vision estimate). Statutory GW wording/casing remains a hard fail.
  - **Frontend**: batch/label-only retakes bind by submitted item id; `ImageDropzone` assigns the next unused panel role after a remove; Vite CSP `connect-src` reads `VITE_API_HOST` via `loadEnv`.
  - **Regression scorer**: empty `results` mappings are refused; `typesize` and `edge` groups are graded (HTTP 500 fails those targets).

- **CORS tightening ported from `-dev` 91e88ff (October 2026)**:
  - Replaced the `*` default allowlist with local Vite origins plus `https://ttb-label-frontend.onrender.com` (the `render.yaml` frontend service).
  - Restricted CORS methods to GET/POST/OPTIONS and headers to Authorization/Content-Type; credentials remain disabled.
  - The demo access-token **dependency stays wired**; it fails open only when `DEMO_ACCESS_TOKEN` is unset. Operators can still set `CORS_ORIGINS=*`.
  - Added `backend/tests/test_cors.py`. `USE_FAST_EXTRACTION` defaults were not changed.

- **Promotion of Features from `-dev` (September 2026)**:
  - **27 CFR 16.22 Option A (Scale-Marker) Type-Size Verification MVP**: Integrated deterministic Computer Vision (OpenCV/Pillow) calibration supporting ISO/IEC 7810 ID-1 cards and printable ArUco fiducials. Measures capital letter heights in mm against statutory volume tiers (1.0 mm / 2.0 mm / 3.0 mm) with confidence margins. Uncalibrated photos retain an honest statutory advisory with zero invented mm.
  - **COLA Application Pre-fill & Template Import (#24)**: Added CSV/JSON template parsing, sample template downloads, and built-in demo presets (Bourbon, Cabernet, IPA, Scotch) with zero-login, fail-open access across Single and Batch Review modes.
  - **Dependency Security Audit & Remediation (#25)**: Bumped `python-multipart` from `0.0.20` to `0.0.32` in `requirements.txt` (remediating PYSEC-2026-1852, PYSEC-2026-3036..3040), applied `npm audit fix` for safe transitive dependency bumps, and documented CVE review rationale.
  - **Comprehensive Governance Documentation**: Added `docs/ALWAYS_ON_HOSTING.md`, `docs/TYPE_SIZE_16_22_DESIGN.md`, `docs/field-tests/typesize/ACCEPTANCE_CHECKLIST.md`, `docs/field-tests/typesize/TYPESIZE_MVP_FIELD_TEST_REPORT.md`, `docs/field-tests/typesize/run_typesize_suite.py`, updated `docs/SBOM.md`, `docs/ERROR_CODES.md`, `docs/PDR.md`, `docs/TECHNICAL_ARCHITECTURE.md`, and added `docs/REGULATORY_REFRENCES.md`.

- **Fail-Open Security Remediation & Abuse Guardrails (September 2026)**:
  - **Soft Rate Limiting (High)**: Integrated `slowapi` inbound rate limiting on expensive review endpoints (`/api/review`, `/api/review/batch`, `/api/review/batch/stream`, `/api/label-check/batch`) keyed by client IP (`X-Forwarded-For` first hop or `request.client.host`). Returns HTTP **429** Too Many Requests with retry advice, NOT 401. Endpoints `/api/health` and `/api/demo-info` remain strictly unlimited.
  - **Concurrency Cap for Claude Calls**: Process-wide `asyncio.Semaphore` (configurable via `MAX_CONCURRENT_CLAUDE_CALLS`, default 5; timeout `CLAUDE_CONCURRENCY_TIMEOUT`, default 30.0s) wrapping Claude vision extractions across single, batch, and streaming endpoints to prevent unbounded-parallel burn of Anthropic API credits. Returns a friendly 503/429 busy message upon timeout — never an auth error.
  - **Spend / Abuse Soft Guardrail**: Implemented `DailySpendGuard` (`MAX_REVIEW_REQUESTS_PER_DAY`) tracking process-wide review volume and logging a loud warning when threshold is exceeded without blocking evaluator access. Documented that multi-instance deployments will require Redis in future iterations.
  - **Hygiene & Hardening**: Explicitly capped Pillow `Image.MAX_IMAGE_PIXELS = 64_000_000` to prevent decompression bombs. Masked raw exception strings in streaming batch responses (`/api/review/batch/stream`) with server-side logs and user-safe failure messages. CORS defaults fail-open to local Vite plus the `render.yaml` frontend origin (not `*`). Verified `DEMO_ACCESS_TOKEN` is left unset for public evaluator demos.
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
  - CORS defaults fail-open to local Vite and the Render frontend from `render.yaml` so evaluator review traffic is not blocked; methods/headers are restricted and credentials stay off.
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
| `CORS_ORIGINS` | Allowed CORS origins (defaults to local Vite + Render frontend, not `*`) | No | `https://ttb-label-frontend.onrender.com` | No |
| `DEMO_ACCESS_TOKEN` | Optional demo access gate token. Dependency stays on review routes; unset = fail-open | No | *(leave unset in public demo)* | Yes |
| `DEMO_USERNAME` | Display username in demo modal | No | `ttb-demo` | No |
| `MAX_REQUEST_BODY_BYTES` | Total HTTP body cap (Content-Length and chunked) | No | `52428800` | No |
| `ENABLE_API_DOCS` | Serve `/docs` `/redoc` `/openapi.json` with a docs-only CSP | No | unset | No |

## External Dependencies

- Anthropic Claude API (`anthropic` Python SDK) for label extraction and vision analysis.

## Known Issues

- Standards of fill check validates against modernized T.D. TTB-200 authorized size lists (§4.72 / §5.203); custom authorized sizes require manual formula verification.
- Exact physical type-size verification (27 CFR 16.22) is automated via Option A (scale marker) when an ID-1 card or ArUco target is co-planar; uncalibrated photos retain the honest statutory physical gauge advisory with zero invented mm.
- Gradio (`6.31.0`) / Streamlit (`1.66.1`) demo pins from `-dev` `#50` are not in this production layout (`tools/gradio_demo`, `tools/eval_dashboard` are excluded on promote). See [SBOM.md](./SBOM.md).

## Operational Notes

- Stateless request lifecycle; no persistent database or image storage (NFR-2).
- Structured latency logs emitted under `RequestTiming`.

## Security Notes

- **Access-token gate stays wired**: `require_demo_access` remains on review routes. When `DEMO_ACCESS_TOKEN` is unset the dependency fails open (public evaluator demos). When set, missing/invalid Bearer tokens return HTTP 401. Do not remove the dependency and do not commit a token.
- **Request body cap**: chunked, missing, or understated `Content-Length` cannot bypass `MAX_REQUEST_BODY_BYTES`. The 413 is sent immediately and fails closed if the cap cannot be read. CORS wraps the limiter (allowed origins get `Access-Control-Allow-Origin` on 413) and security headers are outermost.
- Security response headers set on all requests via `setdefault`. API CSP is `default-src 'none'`; docs HTML (only if `ENABLE_API_DOCS=true`) allows the Swagger CDN.
- Client-facing error messages are sanitized; stack traces logged only on backend. Never log tokens or raw image bytes.

## Next Recommended Actions

1. Integrate with COLA to pull application data directly.
2. Implement state-level requirement extensions.

## Open Questions

- None blocking for current release.

## Handoff Checklist
- [x] Code builds
- [x] Tests pass (476+ backend pytest + 115 frontend vitest + ESLint + `tsc -b` / `vite build`, Oct 30 2026; pins from `-dev` `ceb18a8`)
- [x] Required docs updated (/docs/ERROR_CODES.md, HANDOFF.md, PDR.md, REGULATORY_REFRENCES.md, SBOM.md, TECHNICAL_ARCHITECTURE.md)
- [x] Secrets removed / verified no secrets committed
- [x] Dependencies reviewed
- [x] SBOM updated
- [x] Error codes updated
- [x] Architecture updated
- [x] Regulatory references updated

## What's implemented

- **Calibrated 27 CFR 16.22 Type-Size Verification MVP (Option A Scale Marker)** - automated OpenCV/Pillow calibration using physical scale markers (ISO/IEC 7810 ID-1 standard cards and ArUco targets) to detect capital-letter height in millimeters and enforce statutory volume tiers (1.0 mm / 2.0 mm / 3.0 mm) with explicit confidence intervals. Uncalibrated photos retain an honest statutory advisory with zero invented mm.
- **COLA Application Pre-fill & Template Import** - import application data from CSV or JSON template files, download sample templates, and select built-in demo presets (Bourbon, Cabernet, IPA, Scotch) for zero-login, fail-open testing across single and batch review.
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
