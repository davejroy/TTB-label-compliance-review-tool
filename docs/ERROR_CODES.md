# Error Codes & User-Facing Messages

## Purpose
This document provides a durable reference for error identifiers, failure modes, user-facing error messages returned by the API, troubleshooting, and support handoff.

---

## HTTP Status Codes & Error Mapping

| HTTP Status | Condition | Response Body | Notes |
|-------------|-----------|---------------|-------|
| 200 | Success | JSON / Streaming NDJSON | Operational success |
| 422 | Unprocessable Entity / Validation Error | `{"detail": "..."}` | Client-fixable input or parameter error (per RFC 9110 §15.5.21) |
| 429 | Too Many Requests | `{"detail": "...", "error": "too_many_requests", "retry_after": N}` | Soft inbound IP rate limit exceeded on review routes; includes Retry-After header (never 401/403) |
| 500 | Internal Server Error | `{"detail": "Internal server error"}` | Unexpected backend failure |
| 503 | Service Unavailable / Busy | `{"detail": "..."}` | Concurrency cap acquisition timeout or upstream overload; client retries safely |

*Note on Auth: HTTP 401 / 403 are intentionally NOT used for standard API endpoints as the application operates under a strict Fail-Open / Zero-Login product policy.*

---

## Rate Limiting & Concurrency Error Messages

| Code | Severity | Component | User Message | Internal Meaning | Common Cause | Recommended Action | Retryable | Owner |
|---|---|---|---|---|---|---|---|---|
| RAT-LIM-001 | Warning | Rate Limiter | Rate limit exceeded: {detail}. Please wait a moment before submitting additional label reviews. | Inbound IP request rate exceeded `RATE_LIMIT_REVIEW` (default 20/min) | Rapid successive submissions from same IP | Wait for `Retry-After` window. Health & demo-info remain open. | Yes | User |
| RAT-CONC-001 | Warning | Concurrency Guard | The review service is currently processing a high volume of label requests. Please wait a moment and resubmit. | Process-wide `asyncio.Semaphore` limit reached and timeout expired | High concurrent batch review load | Retry after short delay. `safeFetch` retries automatically. | Yes | User |


---

## Error Code Registry & User-Facing Messages

### Photo / Image Errors

| Code | Severity | Component | User Message | Internal Meaning | Common Cause | Recommended Action | Retryable | Owner |
|---|---|---|---|---|---|---|---|---|
| IMG-DEC-001 | Error | Pillow / Prep | File '{filename}' is not a supported image. Please upload a JPEG, PNG, WEBP, GIF, BMP, or TIFF photo of the label. | Image magic byte signature unrecognized | Non-image or corrupted file uploaded | Upload valid image format | No | Backend |
| IMG-DEC-002 | Error | Pillow / Prep | File '{filename}' could not be read as a valid image. Please submit a new photo. | Pillow failed to decode image bytes | Truncated/corrupted image | Resave/retake photo | No | Backend |
| IMG-SIZE-001 | Error | Upload Guard | File '{filename}' exceeds 10 MB limit. | Upload payload > 10MB | High-res camera uncompressed upload | Resize/compress image before uploading | No | Frontend/User |
| IMG-QTY-001 | Error | Input Guard | A maximum of 4 images may be uploaded per label. You submitted N. Please resubmit with 4 or fewer photos. | Too many images per label | Exceeded MAX_IMAGES_PER_LABEL | Resubmit with ≤4 photos | No | User |

---

### Batch Parameter Validation Errors (HTTP 422)

| Code | Severity | Component | User Message | Internal Meaning | Common Cause | Recommended Action | Retryable | Owner |
|---|---|---|---|---|---|---|---|---|
| VAL-APP-001 | Error | Review Route | Invalid application data. | ApplicationData failed JSON parse or schema | Malformed JSON in form field | Verify ApplicationData schema | No | Frontend |
| VAL-BAT-001 | Error | Batch Route | Invalid batch parameters. | Batch applications failed JSON parse or schema | Malformed JSON array | Check batch parameters | No | Frontend |
| VAL-CNT-001 | Error | Batch Route | image_counts must be a JSON array of positive integers. | image_counts contains 0, negative, or non-int | Malformed image_counts | Pass array like `[1, 2]` | No | Frontend |
| VAL-CNT-002 | Error | Batch Route | Each image count must be <= 4. | An image count exceeds 4 | Single label submitted with >4 photos | Keep each count ≤ 4 | No | Frontend |
| VAL-CNT-003 | Error | Batch Route | image_counts length (X) must match applications length (Y). | Array length mismatch | Batch array length mismatch | Ensure count array matches apps array | No | Frontend |
| VAL-CNT-004 | Error | Batch Route | Sum of image_counts must match the number of uploaded files. | Sum mismatch with files count | Missing or extra uploaded files | Verify sum(image_counts) == len(files) | No | Frontend |
| VAL-BEV-001 | Error | Batch Route | confirmed_beverage_type must be one of: distilled_spirits, wine, beer. | Invalid confirmed beverage enum | Unsupported beverage type string | Use allowed enum values | No | Frontend |
| VAL-ROL-001 | Error | Batch Route | Invalid photo_roles. / photo_roles must be a JSON array of strings. | Malformed photo_roles JSON or non-string item | Malformed photo_roles | Pass array of strings e.g. `["front","back"]` | No | Frontend |

---

### Claude Vision & Extraction Errors (Sanitized Error Surfaces)

| Code | Severity | Component | User Message | Internal Meaning | Common Cause | Recommended Action | Retryable | Owner |
|---|---|---|---|---|---|---|---|---|
| EXT-KEY-001 | Error | Claude Client | API key is invalid or missing. Check the ANTHROPIC_API_KEY environment variable. | AuthenticationError from Anthropic SDK | Missing or invalid API key | Configure valid key in Render/env | No | DevOps |
| EXT-NET-001 | Warning | Claude Client | Network error contacting Claude API. Please try again shortly. | Connection error after retries | Transient network interruption | Retry request | Yes | Backend |
| EXT-RAT-001 | Warning | Claude Client | Anthropic rate limit reached. Please try again shortly. | 429 RateLimitError from Anthropic | High request volume | Retry after short delay | Yes | User |
| EXT-TMO-001 | Warning | Claude Client | The request to Claude timed out. Please try again. | APITimeoutError | Large image or slow network | Retry request | Yes | User |
| EXT-RUN-001 | Error | Extraction | Could not process label image(s). Please submit clearer photos. | Internal extraction runtime error | Low OCR readability or corrupted image buffer | Retake clear photo | No | User |
| EXT-UNK-001 | Error | Extraction | Unexpected error during label extraction. Please try again. | Unhandled exception during extraction | Unexpected model or code error | Inspect backend server logs | Yes | Backend |

---

### Compliance & Extraction Confidence Errors (HTTP 422)

| Code | Severity | Component | User Message | Internal Meaning | Common Cause | Recommended Action | Retryable | Owner |
|---|---|---|---|---|---|---|---|---|
| CMP-CONF-001 | Warning | Compliance Gate | The label image quality is too low to reliably read the required fields (confidence X%, minimum Y% for ...). Please retake the photo... | Overall extraction confidence below threshold | Blurry, dark, or angled photo | Retake photo: flat-on, well-lit, in sharp focus | No | User |
| CMP-CONF-002 | Warning | Compliance Gate | The following label field(s) could not be read clearly enough to produce a reliable compliance result: "brand name"... Please retake the photo... | Per-field confidence fell below threshold | Glare or obstruction on specific field | Retake photo focusing on affected label area | No | User |

---

## Logging Requirements
- Operational metrics (latency, image counts, endpoint paths) are logged under `RequestTiming`.
- Sensitive data (credentials, auth tokens, image binary payloads) must NEVER be logged.
- Internal exception details are logged on the server with stack traces but are NEVER surfaced directly to clients in API responses.

## Troubleshooting Playbooks
1. **Network error contacting Claude API**: Check server egress connectivity to `api.anthropic.com` and Anthropic status dashboard.
2. **Batch parameter validation 422**: Inspect frontend network payload to verify `image_counts` is valid JSON array of ints and sums to `files.length`.
3. **Security Headers Verification**: Run `curl -I https://<host>/api/health` and verify `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and `Content-Security-Policy: default-src 'none'`.

---

*Last updated: September 2026*
