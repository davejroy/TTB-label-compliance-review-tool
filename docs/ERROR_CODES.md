# Error Codes

## Purpose

This document is the durable registry of machine-readable `error_code` values
returned by the TTB Label Compliance Review API. Field users and support staff
should be able to identify what happened, whether to retake a photo, and how
to trace the request.

Every error response and every review/label-check body with
`overall_status` of `retake` or `error` includes:

| Field | Meaning |
|---|---|
| `error_code` | Stable token such as `PHOTO_QUALITY_LOW` |
| `message` | Scrubbed, human-readable text |
| `request_id` | Per-request correlation id |

The same `request_id` is echoed in the `X-Request-ID` response header and in
server log lines. Clients may send `X-Request-ID` only when it matches
`^[A-Za-z0-9-]{1,64}$`; otherwise the server generates a fresh id.

HTTP error bodies use a fixed user-safe `message` plus `request_id`. Parser
and exception text is written to server logs only.

Photo-quality failures also include:

```json
"retake": { "field": "ABV", "area": "front", "cause": "glare", "tip": "Hold the phone parallel to the label and avoid overhead light glare." }
```

The top-line `message` is exactly `Photo quality is too low, please retake.`
whenever a retake is required.

## Error Code Format

Codes are uppercase `SNAKE_CASE` tokens. They are stable identifiers: clients
must branch on `error_code` and `overall_status`, never on message strings.

## Status Separation

`overall_status` values:

| Value | Meaning |
|---|---|
| `pass` | All evaluated checks passed |
| `warning` | Human review recommended; not a hard violation |
| `fail` | The label violates a TTB rule (unchanged semantics) |
| `retake` | The photo could not be read; recapture is required |
| `error` | The system or the upload failed |

A missing Government Warning is `fail` (absent), not `retake`. An unreadable
warning on an otherwise readable label is `retake` with `field_status` of
`unreadable` for that element.

## Field Status

`field_status` maps each required element to `present`, `absent`, or
`unreadable`.

| Value | Meaning |
|---|---|
| `present` | The element was read with enough confidence to evaluate |
| `absent` | The element is confidently not on the label (a possible violation) |
| `unreadable` | The element could not be read; never treat this as absent |

An empty required field scored `0.0` on a readable photo (overall
confidence at or above the class threshold) is `absent`. The compliance
result is `fail` with "X not found on label", not a photo retake. A
low-but-nonzero score (about `0.10`–`0.30`) stays `unreadable` and still
requests a retake. Required elements: brand, class/type, ABV, net
contents, warning, name and address.

Blank photos return `The submitted image appears to be blank. Please upload a photo of an alcohol beverage label.` and a retake tip that the label is not in the frame / nothing printed is visible (not a glare tip). Non-label photos return `The submitted image does not appear to be an alcohol beverage label.` Application and batch form errors return HTTP 422 and name the invalid field (for example `field 'brand_name' is missing or invalid`). File-quality problems that still produce a review body stay HTTP 200 with `overall_status` of `retake` or `error`.

## Retake Causes

| Cause | Typical tip |
|---|---|
| `blur` | Hold the phone still and tap the label to focus before you take the photo. |
| `glare` | Hold the phone parallel to the label and avoid overhead light glare. |
| `blank` | No label is visible in the frame. Photograph the printed label so text is readable. Solid-white / empty frames use `cause=blank` and the blank-photo message (not glare wording). |
| `dark` | Move to brighter, even lighting or turn on a room light behind the camera. |
| `angle` | Hold the phone parallel to the label so the text is not skewed. |
| `curvature` | Flatten the label in the frame or photograph the curved panel straight-on. |
| `cropped` | Include the entire required statement in the frame, then retake. |
| `low_resolution` | Move closer to the label and retake at full camera resolution. |

`area` is `front`, `back`, or `neck`. `field` is the required element that
could not be read (warning, ABV, net contents, brand, class/type, name and
address, or `label` when the whole photo is unreadable).

## Error Code Registry

| Code | Severity | Component | User Message | Internal Meaning | Common Cause | Recommended Action | Retryable | Owner |
|---|---|---|---|---|---|---|---|---|
| PHOTO_QUALITY_LOW | User-fixable | Review / image intake | Photo quality is too low, please retake. | Whole-image confidence below threshold, corrupt decode, resolution too low, or extreme pre-gate (blank / black / tiny) | Blur, darkness, glare, tiny thumbnail, truncated file | Retake using the `retake` tip | Yes (new photo) | Review API |
| PHOTO_UNREADABLE_FIELD | User-fixable | Review / extraction | Photo quality is too low, please retake. | One or more required fields scored below per-field threshold | Glare or crop on a single panel | Retake the named `area`; readable findings are still returned | Yes (new photo) | Review API |
| UNSUPPORTED_FILE | User-fixable | Upload validation | File is not a supported image. Please upload a JPEG, PNG, WEBP, GIF, BMP, TIFF, HEIC, or HEIF photo of the label. | Signature is not JPEG/PNG/WEBP/GIF/BMP/TIFF/HEIC/HEIF | PDF, video, or renamed text file | Save as a supported still-image format and resubmit | Yes (new file) | Review API |
| FILE_TOO_LARGE | User-fixable | Upload validation | The upload is too large. Please use fewer or smaller photos. (HTTP 413 request-body cap; a single file over 10 MB may instead say File 'name' exceeds 10 MB limit.) | Request body or per-file payload larger than the configured cap | Chunked or large multipart upload, high-resolution burst | Use fewer or smaller photos, then resubmit | Yes (new file) | Review API |
| VISION_UNAVAILABLE | Transient / config | Extraction | The label reader is unavailable right now — your photo is fine. Try again shortly. | Missing, invalid, or revoked model API key (aliases: `CONFIG_ERROR`, `MODEL_UNAVAILABLE`) | Key unset or 401 | Do not retake the photo; set a valid key and retry | Yes (after config) | Review API |
| RATE_LIMITED | Transient | Ingress / upstream | The label reader is busy — your photo is fine. Wait a moment and try again. (Inbound IP cap may still say “Too many requests.”) | Inbound IP limit, concurrency busy, or upstream 429/529 | Burst of reviews or provider overload | Wait and retry the same photos | Yes | Review API |
| UPSTREAM_TIMEOUT | Transient | Extraction | The label reader timed out — your photo is fine. Try again shortly. | Vision request timed out after one retry | Slow upstream or large image | Retry the same photos | Yes | Review API |
| UPSTREAM_ERROR | Transient | Extraction | The label reader is unavailable right now — your photo is fine. Try again shortly. | Upstream 5xx or connection error | Provider outage | Retry later; do not expose provider bodies | Yes | Review API |
| INTERNAL_ERROR | System | API | An unexpected error occurred. Please try again. | Unhandled exception | Defect | Retry; send `request_id` to support | Maybe | Review API |
| INVALID_REQUEST | User-fixable | API / templates | Request could not be processed (malformed JSON, too many images, bad template). Batch `image_counts` uses the scrubbed message `image_counts must be a JSON list of integers.` | 400 / 422 validation | Bad form fields, CSV, or non-JSON `image_counts` | Fix the payload | Yes (fixed payload) | Review API |
| AUTH_REQUIRED | Access | Demo gate | Missing or invalid access token | 401 when the optional demo gate is enabled | Missing `Authorization` | Supply a valid token | No | Auth |

## HTTP/API Error Mapping

| HTTP Status | Internal Code | Condition | Response Body | Notes |
|---|---|---|---|---|
| 200 | (none) | `pass` / `warning` / `fail` | Existing review or label-check body plus `request_id` and `field_status` | Compliance fail is not remapped |
| 200 | PHOTO_QUALITY_LOW | Whole-image unreadable or tiny/corrupt photo | `overall_status=retake`, `message`, `retake`, `request_id` | Never HTTP 500 |
| 200 | PHOTO_UNREADABLE_FIELD | Some required fields unreadable | `overall_status=retake`, partial `fields`/`checks`, `retake` | LowConfidenceError path stays HTTP 200 |
| 200 | UNSUPPORTED_FILE / FILE_TOO_LARGE / INVALID_REQUEST | Bad upload that still returns a result body | `overall_status=error` | Additive; clients already accepted 200 + `error` |
| 200 | INTERNAL_ERROR (legacy path only) | Unexpected extraction failure that is not a vision/upstream code | `overall_status=error` | Message is scrubbed; vision/auth/timeout/429 no longer use this path |
| 400 | INVALID_REQUEST | Malformed application or batch parameters | `{error_code,message,request_id,detail}` | Fixed message; parser text stays in logs |
| 401 | AUTH_REQUIRED | Demo gate rejected the request | Same contract | Public demo remains ungated when the token is unset. The frontend clears the stored token and shows the Demo access login card |
| 413 | FILE_TOO_LARGE | Request body or template exceeds the configured cap | Same contract | Enforced on bytes actually read (not only `Content-Length`), including chunked bodies. 413 is sent as soon as the cap is crossed; leftover chunks are not drained. If the cap cannot be read the limiter fails closed (413) instead of accepting an unlimited body. CORS wraps the size limiter so allowed origins receive `Access-Control-Allow-Origin` on 413; security headers wrap CORS so preflight still gets CSP/HSTS. The frontend unwraps single, batch, and stream wrappers and shows the structured FILE_TOO_LARGE text (never a proxy HTML 413 page). `MAX_REQUEST_BODY_BYTES` / `MAX_TEMPLATE_BYTES`; `GET`/`HEAD` `/api/health` is never blocked; other methods on that path are still capped |
| 422 | INVALID_REQUEST | Unprocessable template or validation error | Same contract | Fixed message; parser text stays in logs |
| 429 | RATE_LIMITED | Per-IP rate limit, daily cap, failed-auth lockout, or upstream 429/529 | Contract plus `error=too_many_requests` and `retry_after` when the inbound cap trips | Cap trips log `cap.per_ip`, `cap.daily_soft`, `cap.global_daily`, or `cap.auth_lockout`. Guards fail open. Upstream 429/529 uses the photo-is-fine message |
| 500 | INTERNAL_ERROR | Unhandled exception | Contract only; no stack or path | Logged server-side with `request_id` |
| 502 | UPSTREAM_ERROR | Upstream 5xx or connection error | `{error_code,message,request_id,detail}` | Never a 200 pass/fail/pre-fill body |
| 503 | VISION_UNAVAILABLE | Missing or rejected model API key | `{error_code,message,request_id,detail}` | `/api/health` exposes `vision_configured` (boolean only). Photo is fine; do not retake |
| 504 | UPSTREAM_TIMEOUT | Vision request timed out after one retry | `{error_code,message,request_id,detail}` | Streaming batch raises this on the first label instead of a 200 NDJSON “finished” stream |

## Examples

### Retake — unreadable field, partial results

```http
HTTP/1.1 200 OK
X-Request-ID: 7f3c2a91b0e64d1e9c8a1f2d3e4b5c6a
```

```json
{
  "overall_status": "retake",
  "error_code": "PHOTO_UNREADABLE_FIELD",
  "error": "Photo quality is too low, please retake.",
  "request_id": "7f3c2a91b0e64d1e9c8a1f2d3e4b5c6a",
  "retake": {
    "field": "name and address",
    "area": "front",
    "cause": "blur",
    "tip": "Hold the phone still and tap the label to focus before you take the photo."
  },
  "field_status": {
    "brand_name": "present",
    "class_type": "present",
    "alcohol_content": "present",
    "net_contents": "present",
    "government_warning": "present",
    "name_and_address": "unreadable"
  },
  "fields": [
    {"field": "brand_name", "status": "pass", "label_name": "Brand Name", "message": "Brand name matches the application."}
  ]
}
```

### Error — unsupported file

```json
{
  "overall_status": "error",
  "error_code": "UNSUPPORTED_FILE",
  "error": "File 'notes.jpg' is not a supported image. Please upload a JPEG, PNG, WEBP, GIF, BMP, TIFF, HEIC, or HEIF photo of the label.",
  "request_id": "a1b2c3d4e5f64789a0b1c2d3e4f50617",
  "retake": null,
  "fields": []
}
```

### Fail — real compliance violation (unchanged)

```json
{
  "overall_status": "fail",
  "error_code": null,
  "error": null,
  "field_status": {
    "government_warning": "absent"
  }
}
```

### HTTP 429

```json
{
  "error_code": "RATE_LIMITED",
  "message": "Rate limit exceeded: 2 per 1 minute. Please wait a moment before submitting additional label reviews.",
  "detail": "Rate limit exceeded: 2 per 1 minute. Please wait a moment before submitting additional label reviews.",
  "request_id": "c0ffee12aabbccddeeff001122334455",
  "error": "too_many_requests",
  "retry_after": 60
}
```

## Logging Requirements

- Every request generates or accepts a `request_id` and logs it on timing and
  error lines (`request_id=<id> ...`).
- Log technical detail server-side only: exception type, upstream status,
  file size, confidence scores.
- Never log image bytes, authorization secrets, API keys, or full provider
  error bodies in user-facing fields.
- User-facing `message` / `error` values are scrubbed of stack traces, file
  paths, model identifiers, key fragments, and provider names.

## Troubleshooting Playbooks

### Photo quality / retake

1. Read `retake.field`, `retake.area`, `retake.cause`, and `retake.tip`.
2. Recapture that panel (front, back, or neck). Hold the phone parallel to
   the label; avoid overhead glare; fill the frame.
3. Resubmit. Readable findings from the first pass are informational only;
   run a new review after the retake.

### Unsupported or oversized file

1. Confirm the file is a still image. The client dropzone accepts JPEG, PNG, WEBP, or HEIC. The server still re-checks magic bytes.
2. iPhone HEIC/HEIF is accepted when the server can decode it; otherwise convert to JPEG.

### Vision unavailable (missing or rejected key)

1. Do not retake the photo. The message says the photo is fine.
2. Confirm `/api/health` `vision_configured` is true.
3. Retry after a valid key is set. Give support the `request_id` if it persists.

### Rate limited

1. Honor `Retry-After`.
2. Health and demo-info endpoints stay unlimited.
3. Daily per-IP / global soft caps return HTTP 429 with this same contract.
   If the counter itself errors, the request is allowed through (fail-open).

### Upstream timeout or error

1. Retry once. The server already retried transient upstream failures.
2. If it persists, give support the `request_id`.

### Internal error

1. Confirm the client did not receive a stack trace or file path.
2. Escalate with the `request_id`. Do not ask the user to interpret logs.

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-10-31 | Body cap: only GET/HEAD `/api/health` is exempt; POST/PUT on that path are still capped. Early 413 adopts a safe incoming X-Request-ID. | Maintainer |
| 2026-10-31 | Vision/auth/timeout/429 leave as typed HTTP 502/503/504/429 (`VISION_UNAVAILABLE`, `UPSTREAM_ERROR`, `UPSTREAM_TIMEOUT`, `RATE_LIMITED`). Never 200 pass/fail/pre-fill. CSV Comments column + formula-injection escaping | Maintainer |
| 2026-10-31 | Blank/empty photos use `cause=blank` and blank-photo wording (not glare). Docs routes get a Swagger-safe CSP; API routes keep `default-src 'none'` | Maintainer |
| 2026-10-30 | Frontend unwraps Batch review/stream 413 wrappers; body-cap lookup fails closed; CORS refuses wildcard+credentials | Maintainer |
| 2026-10-30 | 413 sent immediately without draining leftover chunks; frontend ignores proxy HTML 413; preflight keeps security headers | Maintainer |
| 2026-10-28 | HTTP 413 now counts actual body bytes (chunked / missing / understated Content-Length) and carries CORS headers for allowed origins | Maintainer |
| 2026-10-28 | Blank/washed-out photos keep the blank message; retake tip no longer blames glare | Maintainer |
| 2026-10-24 | Q1 hardening: HTTP 413 for body/template caps; 429 for auth lockout; fixed error messages; validated `X-Request-ID` | Maintainer |
| 2026-10-23 | Photo quality pre-gate: `PHOTO_QUALITY_LOW` extreme short-circuit; HEIC/HEIF now supported; scrubbed `image_counts` INVALID_REQUEST message | Maintainer |
| 2026-10-23 | Daily soft cap now returns HTTP 429 / RATE_LIMITED; fail-open only when the counter errors | Maintainer |
| 2026-10-23 | Rewrote the contract: stable `error_code`, `request_id`, `retake` object, `overall_status` retake/error, `field_status`, scrubbed messages | Maintainer |
| 2026-10-01 | Added Gradio demo and Eval Dashboard error handling and connection error codes | Maintainer |
| 2026-09-18 | Added fallback `cannot_measure` entry for unhandled CV exceptions during type-size evaluation | Maintainer |
| 2026-09-16 | Added rate limiting and daily spend guard error codes | Maintainer |
| 2026-09-09 | Added image quality diagnostics and low confidence error codes | Maintainer |
