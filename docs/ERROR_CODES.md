# Error Codes & User-Facing Messages

This document lists all user-facing error messages returned by the API, their causes, and recommended remediation steps.

---

## Photo / Image Errors

| Error Message | Cause | Remediation |
|---------------|-------|-------------|
| Photo quality is too low to read. The file could not be decoded as a valid image. Please submit a new, clear photo saved as JPEG or PNG. | The uploaded file is corrupt, truncated, or in an unsupported format. | Re-take the photo and save as JPEG or PNG. Avoid WebP, HEIC, or RAW formats. |
| Photo quality is too low to read. The image is only WxH pixels, which is too small for reliable text recognition. Please submit a new photo taken closer to the label. | The image resolution is below the minimum threshold (100x100 px). | Move the camera closer to the label and re-take the photo. |
| File FILENAME exceeds 10 MB limit. | The uploaded file is larger than 10 MB. | Compress the image or reduce its resolution before uploading. |
| File FILENAME could not be read as a valid image. Please submit a new photo. | An unexpected decode error occurred after passing initial format checks. | Try re-saving the image in a different format and re-uploading. |
| A maximum of 4 images may be uploaded per label. You submitted N. Please resubmit with 4 or fewer photos. | More than 4 images were submitted for a single label. | Split your submission and upload at most 4 photos per label review. |

---

## Application Data & COLA Template Import Errors

| Error Message | Cause | Remediation |
|---------------|-------|-------------|
| Invalid application data: {detail} | The application form field contains malformed JSON or does not conform to the expected schema. | Validate the JSON structure against the ApplicationData model. |
| CSV file is empty. / JSON file is empty. | Uploaded template file contains 0 bytes. | Upload a valid CSV or JSON file containing COLA records. |
| CSV file is missing a header row. / CSV file contains headers but has no data rows. | Uploaded CSV has no column headers or data rows. | Ensure the first row has standard column headers (brand_name, class_type, etc.) and contains data rows. |
| Missing required application field(s): {fields}. | One or more mandatory fields (`beverage_type`, `brand_name`, `class_type`, `alcohol_content`, `net_contents`) are missing or blank. | Fill in the missing application columns in the CSV or JSON file. |
| Invalid beverage type '{val}'. Expected 'distilled_spirits', 'wine', or 'beer'. | Beverage type column contains an unrecognized classification. | Use one of the canonical types (`distilled_spirits`, `wine`, `beer`) or accepted aliases (`spirits`, `malt beverage`, etc.). |
| Malformed JSON syntax (line X, col Y): {msg} | Uploaded JSON template contains syntax errors. | Validate JSON syntax in an editor before re-uploading. |
| Invalid image_counts: {detail} | The image_counts field (batch endpoint) contains invalid JSON or non-integer values. | Provide a JSON array of positive integers, e.g. [2, 1, 3]. |
| Sum of image_counts must match the number of uploaded files. | The total of all image_counts values does not equal the number of files uploaded. | Ensure sum(image_counts) == len(files). |
| Failed to parse CSV/JSON pre-fill data: {detail} | Uploaded pre-fill file is malformed, has invalid headers, or contains unparsable rows. | Check CSV header column names (`brand_name`, `class_type`, `alcohol_content`, `net_contents`) or JSON structure and re-import. |

---

## Claude API & Concurrency Errors

| Error Message | Cause | Remediation |
|---------------|-------|-------------|
| API key is invalid or missing. Check the ANTHROPIC_API_KEY environment variable. | The backend Anthropic API key is not configured or has been revoked. | Set the ANTHROPIC_API_KEY environment variable to a valid key. |
| The review service is currently processing a high volume of label requests. Please wait a moment and resubmit. | Process-wide Claude concurrency limit reached and queue timeout expired. | Retry after a few seconds. Client `safeFetch` automatically retries 429/503/529. |
| Claude API error after retries: ... | A transient or persistent Anthropic API error occurred after one automatic retry. | Retry the request. If the error persists, check the Anthropic status page. |

---

## Rate Limiting & Abuse Prevention

| Error Message | Cause | Remediation |
|---------------|-------|-------------|
| Rate limit exceeded: {detail}. Please wait a moment before submitting additional label reviews. | Inbound IP request rate exceeded the configured threshold on review routes (`RATE_LIMIT_REVIEW`, default 20/min). | Wait for the period indicated in the `Retry-After` header before issuing new review requests. Frontend UI still loads and health/demo-info endpoints remain open. |

---

## 27 CFR 16.22 Type-Size Verification & Scale-Marker Messages

| State | Message / Condition | Cause | Remediation |
|---|---|---|---|
| `cannot_measure` | Scale marker not detected in photo. Please place a standard ISO/IEC 7810 ID-1 card (e.g. driver's license, credit card) or calibrated marker beside the Government Warning and retake the photo. | Physical scale marker was omitted, occluded, or outside the camera frame. | Place a standard ID card or ArUco target flush and co-planar beside the Government Warning and retake the photo. |
| `cannot_measure` | Scale marker perspective tilt (X°) exceeds maximum 25.0° threshold. Hold the camera parallel and flat-on to the label surface to avoid foreshortening errors, then retake the photo. | Camera was held at an oblique perspective angle (>25° planar pitch/yaw). | Hold the camera flat-on and perpendicular to the label surface. |
| `cannot_measure` | Scale marker detected, but Government Warning text glyphs could not be segmented cleanly. Ensure the warning text is in sharp focus and free of glare or shadows, then retake the photo. | Warning text is out of focus, motion-blurred, or obscured by specular glare or deep shadows. | Ensure steady lighting without flash glare, tap to focus on the text, and retake the photo. |
| `cannot_measure` (Fallback) | Type-size evaluation encountered an unexpected condition ({ExceptionType}). Physical gauge measurement recommended. | Unhandled CV exception or corrupt image segment during type-size evaluation caught by defensive handlers. | System fails open without HTTP 500. Verification degrades gracefully to advisory mode. Re-take photo if automated calibration is needed. |
| `fail` | Government Warning statement text is valid, but type size is deficient: Calibrated capital letter height X.XX mm ± Y.YY mm is below the statutory 27 CFR 16.22 requirement (Z.Z mm)... | Upper-case letter height is below the 27 CFR 16.22 statutory minimum for the container volume tier. | Re-typeset the Government Warning text to meet or exceed the statutory minimum millimeter height for the container capacity. |
| `warning` | Calibrated capital letter height X.XX mm ± Y.YY mm is borderline near the Z.Z mm threshold. Physical gauge verification recommended. | Measured capital height upper confidence bound overlaps the statutory threshold. | Perform manual verification using a physical optical comparator or certified millimeter gauge. |
| `warning` (Uncalibrated) | Uncalibrated photo without physical scale marker. Statutory threshold is >=X.X mm based on container volume... Physical gauge measurement required to verify 27 CFR 16.22 compliance. | Photo uploaded without a scale marker. | For automated type-size calibration, include an ID-1 card or ArUco marker in the photo, or verify using a physical gauge. |

---

## HTTP Status Codes

| Code | Meaning |
|------|---------|
| 200  | Success |
| 400  | Bad request - malformed input data |
| 401  | Unauthorized - missing or invalid demo access token when DEMO_ACCESS_TOKEN gate is active (never returned for rate limits or normal public evaluator access) |
| 422  | Unprocessable entity - user-fixable input problem (per RFC 9110 Section 15.5.21) |
| 429  | Too Many Requests - soft inbound IP rate limit exceeded; returns actionable retry message |
| 500  | Internal server error - unexpected backend failure |
| 503  | Service Unavailable - concurrency cap timeout or temporary upstream outage |

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-09-18 | Added fallback `cannot_measure` entry for unhandled CV exceptions during type-size evaluation | Kilroy_Lives |
| 2026-09-16 | Added rate limiting and daily spend guard error codes | Kilroy_Lives |
| 2026-09-09 | Added image quality diagnostics and low confidence error codes | Kilroy_Lives |

---

*Last updated: September 18, 2026*
