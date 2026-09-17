# Configuration Reference

This document describes every runtime configuration input for the TTB Label
Compliance Review backend: environment variables (deployment-time) and the
admin-tunable code constants that govern image quality and compliance scoring.

Related documents: [TECHNICAL_ARCHITECTURE.md](./TECHNICAL_ARCHITECTURE.md),
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
| CORS_ORIGINS | No | "" (empty -> `*`) | Comma-separated list of allowed browser origins. When unset, defaults fail-open to `*` with a log warning so public evaluator demos and preview URLs are never blocked. For production lock-down, set to the exact frontend URL (e.g. `https://ttb-label-frontend.onrender.com`). |
| RATE_LIMIT_REVIEW | No | `20/minute` | Inbound rate limit on expensive review endpoints (`/api/review`, `/api/review/batch`, `/api/review/batch/stream`, `/api/label-check/batch`) keyed by client IP (`X-Forwarded-For` first hop). Exceeding limit returns HTTP 429 (Too Many Requests), NEVER 401. `/api/health` and `/api/demo-info` remain unlimited. |
| MAX_CONCURRENT_CLAUDE_CALLS | No | `5` | Process-wide `asyncio.Semaphore` limit for concurrent Claude extraction calls. Prevents batch floods from burning API capacity. |
| CLAUDE_CONCURRENCY_TIMEOUT | No | `30.0` | Seconds to wait for a Claude concurrency permit before returning a friendly 503/429 busy message. |
| MAX_REVIEW_REQUESTS_PER_DAY | No | `0` (disabled) | Soft spend/abuse daily request threshold for single-instance deployments. Logs a loud warning when crossed without blocking evaluator access. Multi-instance setups will require Redis later. |
| DEMO_ACCESS_TOKEN | No | "" (unset) | Bearer token for the demo access gate. **Leave unset for public TTB evaluator demos** (fails open so evaluators are never locked out). Never enable by default. Do NOT add API_AUTH_TOKEN. |
| DEMO_USERNAME | No | "ttb-demo" | Optional display/username paired with the demo access gate. |

Defined in: backend/app/auth.py (DEMO_ACCESS_TOKEN, DEMO_USERNAME),
backend/app/claude_client.py (CLAUDE_MODEL, USE_FAST_EXTRACTION, EXTRACTION_FAST_MODEL),
backend/app/limiter.py (RATE_LIMIT_REVIEW, MAX_REVIEW_REQUESTS_PER_DAY),
backend/app/main.py (ANTHROPIC_API_KEY, CORS_ORIGINS, MAX_CONCURRENT_CLAUDE_CALLS, CLAUDE_CONCURRENCY_TIMEOUT).

## Upload Limits (backend/app/main.py)

| Constant | Value | Meaning |
|---|---|---|
| MAX_FILE_SIZE | 10 MB per image | Uploads exceeding this are rejected. |
| MAX_IMAGES_PER_LABEL | 4 | Maximum images accepted per label. |

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
