# Configuration Reference

This document describes every runtime configuration input for the TTB Label
Compliance Review backend: environment variables (deployment-time) and the
admin-tunable code constants that govern image quality and compliance scoring.

Related documents: [ALWAYS_ON_HOSTING.md](./ALWAYS_ON_HOSTING.md), [TECHNICAL_ARCHITECTURE.md](./TECHNICAL_ARCHITECTURE.md),
[DEPLOYMENT.md](./DEPLOYMENT.md), [ERROR_CODES.md](./ERROR_CODES.md),
[SECURITY_REVIEW_2026-09-16.md](./SECURITY_REVIEW_2026-09-16.md),
[SECURITY_REVIEW_OWASP_LLM.md](./SECURITY_REVIEW_OWASP_LLM.md).

## Environment Variables

All secrets are supplied via the environment only; none are stored in the repo.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| ANTHROPIC_API_KEY | Yes (for live review) | (none) | Anthropic API key used by the Claude client. If unset/invalid the review endpoints return a clear error. |
| CLAUDE_MODEL | No | claude-sonnet-4-5 | Model identifier passed to the Anthropic API (e.g. `claude-sonnet-4-6`). |
| USE_FAST_EXTRACTION | No | false | Boolean flag (`true`/`false`). When enabled (`true`), routes extractions to a faster/cheaper model for `-dev` or rapid iteration. Default is `false` (production Sonnet accuracy preserved). |
| EXTRACTION_FAST_MODEL | No | claude-3-5-haiku-latest | Fast Claude model to use when `USE_FAST_EXTRACTION=true`. |
| CORS_ORIGINS | No | local Vite + `https://ttb-label-frontend.onrender.com` | Comma-separated list of allowed browser origins. When unset, defaults fail-open to `http://localhost:5173`, `http://127.0.0.1:5173`, and the Render frontend service from `render.yaml` (not `*`). Allowed methods are GET/POST/OPTIONS; allowed headers are Authorization and Content-Type; credentials are disabled. Set to the exact frontend URL to lock down, or `*` to restore a wildcard. |
| RATE_LIMIT_REVIEW | No | `20/minute` | Inbound rate limit on expensive review endpoints (`/api/review`, `/api/review/batch`, `/api/review/batch/stream`, `/api/label-check/batch`) keyed by client IP (`X-Forwarded-For` first hop). Exceeding limit returns HTTP 429 (Too Many Requests), NEVER 401. `/api/health` and `/api/demo-info` remain unlimited. |
| MAX_CONCURRENT_CLAUDE_CALLS | No | `5` | Process-wide `asyncio.Semaphore` limit for concurrent Claude extraction calls. Prevents batch floods from burning API capacity. |
| CLAUDE_CONCURRENCY_TIMEOUT | No | `30.0` | Seconds to wait for a Claude concurrency permit before returning a friendly 503/429 busy message. |
| MAX_REVIEW_REQUESTS_PER_DAY | No | `0` (disabled) | Soft spend/abuse daily request threshold for single-instance deployments. Logs a loud warning when crossed without blocking evaluator access. Multi-instance setups will require Redis later. |
| DEMO_ACCESS_TOKEN | No | "" (unset) | Bearer token for the demo access gate. The `require_demo_access` dependency **must stay wired** on review routes. **Leave unset for public TTB evaluator demos** (fails open so evaluators are never locked out). Never enable by default. Do NOT add API_AUTH_TOKEN. Never commit this value. |
| DEMO_USERNAME | No | "ttb-demo" | Optional display/username paired with the demo access gate. |
| MAX_REQUEST_BODY_BYTES | No | `52428800` (50 MB) | Hard cap on the entire HTTP request body. Enforced from `Content-Length` and by counting streamed / chunked ASGI receive bytes so chunked, missing, or understated `Content-Length` cannot bypass the limit. The 413 is sent as soon as the cap is crossed (leftover chunks are not drained). `/api/health` is never blocked. Invalid values fall back to the default; if the cap cannot be read at all the limiter fails closed (HTTP 413) instead of accepting an unlimited body. |
| MAX_LABELS_PER_BATCH | No | `50` | Maximum labels accepted on `/api/review/batch` and `/api/review/batch/stream`. |
| MAX_TEMPLATE_BYTES | No | `1048576` (1 MB) | Maximum COLA template upload size. |
| ENABLE_API_DOCS | No | unset (off) | When `true`/`1`/`yes`/`on`, serves `/docs`, `/redoc`, and `/openapi.json`. Those HTML pages get a separate CSP that allows the FastAPI/Swagger CDN (`cdn.jsdelivr.net`). JSON API routes keep `default-src 'none'`. |

Defined in: backend/app/auth.py (DEMO_ACCESS_TOKEN, DEMO_USERNAME),
backend/app/claude_client.py (CLAUDE_MODEL, USE_FAST_EXTRACTION, EXTRACTION_FAST_MODEL),
backend/app/limiter.py (RATE_LIMIT_REVIEW, MAX_REVIEW_REQUESTS_PER_DAY),
backend/app/request_limits.py (MAX_REQUEST_BODY_BYTES, MAX_LABELS_PER_BATCH, MAX_TEMPLATE_BYTES),
backend/app/security.py (API_CSP, DOCS_CSP),
backend/app/main.py (ANTHROPIC_API_KEY, CORS_ORIGINS, MAX_CONCURRENT_CLAUDE_CALLS, CLAUDE_CONCURRENCY_TIMEOUT, ENABLE_API_DOCS).

## Upload Limits (backend/app/request_limits.py)

| Constant | Value | Meaning |
|---|---|---|
| MAX_FILE_SIZE | 10 MB per image | Per-file uploads exceeding this are rejected (`FILE_TOO_LARGE`). |
| MAX_IMAGES_PER_LABEL | 4 | Maximum images accepted per label. |
| DEFAULT_MAX_REQUEST_BODY_BYTES | 50 MB | Total request body cap, including chunked uploads without Content-Length. |
| DEFAULT_MAX_LABELS_PER_BATCH | 50 | Batch / stream label cap. |

Starlette middleware is LIFO (last added = outermost). Production order is `RequestBodyLimitMiddleware` (innermost) → `SecurityHeadersMiddleware` → `CORSMiddleware` (outermost) so HTTP 413 still receives `Access-Control-Allow-Origin` and security headers.

## Admin-Tunable Image-Quality Thresholds (backend/app/claude_client.py)

These constants let an administrator relax or tighten the readability/reliability
gate without touching program logic. The design goal is to enhance a photo when
possible and only request a retake when quality falls below the floor.

| Constant | Value | Effect |
|---|---|---|
| MIN_PIXEL_AREA | 10000 (100x100) | Images smaller than this are treated as too small to read. |
| MAX_IMAGE_DIMENSION | 1600 | Larger images are downscaled before analysis (speed). |
| JPEG_QUALITY | 82 | Re-encode quality for the enhanced image. |
| CLAUDE_TIMEOUT | 60.0 s | Per-request timeout to the Anthropic API. |
| FOCUS_RETAKE_FLOOR | 35.0 | Laplacian variance below this = out of focus. Mild blur (~55) is enhanced; severe (~20) triggers retake. |
| DARK_FLOOR | 90.0 | Mean luma below this = too dark (lighting retake guidance). |
| BRIGHT_CEILING | 235.0 | Mean luma above this = overexposed. |
| GLARE_CLIP_FRACTION | 0.06 | More than 6% near-white pixels = glare/reflection. |
| COVERAGE_MIN_STDDEV | 25.0 | Very low contrast = label too small or blank in frame. |

## 27 CFR 16.22 Type-Size Constants (backend/app/cv_typesize.py)

These constants govern the computer vision scale-marker detection and millimeter height calculations.

| Constant | Value | Meaning |
|---|---|---|
| ID1_WIDTH_MM | 85.60 mm | Standard ISO/IEC 7810 ID-1 card long edge dimension used as scale reference. |
| ID1_HEIGHT_MM | 53.98 mm | Standard ISO/IEC 7810 ID-1 card short edge dimension. |
| ID1_ASPECT_RATIO | ~1.5858 | Target aspect ratio for ID-1 card contour detection. |
| MAX_SKEW_ANGLE_DEG | 25.0° | Maximum allowable perspective tilt angle before triggering `cannot_measure` retake prompt. |

## Compliance Scoring Constants (backend/app/compliance.py)

| Constant | Value | Meaning |
|---|---|---|
| _FILL_ML_TOLERANCE | 1.0 | Allowed net-contents tolerance in mL. |
| _FILL_FLOZ_TOLERANCE | 0.1 | Allowed net-contents tolerance in fl oz. |
| FIELD_CONFIDENCE_THRESHOLDS (default) | 0.45 | Minimum per-field extraction confidence before a field is trusted. |

## How to Change a Threshold

1. Edit the constant in the referenced module.
2. Run the backend test suite: `cd backend && python -m pytest -q`.
3. Confirm the fixture-driven quality tests still reflect the intended behavior.
4. Commit with a conventional message, e.g. `chore(config): relax FOCUS_RETAKE_FLOOR`.
