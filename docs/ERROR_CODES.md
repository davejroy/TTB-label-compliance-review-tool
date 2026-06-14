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

## Application Data Errors

| Error Message | Cause | Remediation |
|---------------|-------|-------------|
| Invalid application data: {detail} | The application form field contains malformed JSON or does not conform to the expected schema. | Validate the JSON structure against the ApplicationData model. |
| Invalid image_counts: {detail} | The image_counts field (batch endpoint) contains invalid JSON or non-integer values. | Provide a JSON array of positive integers, e.g. [2, 1, 3]. |
| Sum of image_counts must match the number of uploaded files. | The total of all image_counts values does not equal the number of files uploaded. | Ensure sum(image_counts) == len(files). |

---

## Claude API Errors

| Error Message | Cause | Remediation |
|---------------|-------|-------------|
| API key is invalid or missing. Check the ANTHROPIC_API_KEY environment variable. | The backend Anthropic API key is not configured or has been revoked. | Set the ANTHROPIC_API_KEY environment variable to a valid key. |
| Claude API error after retries: ... | A transient or persistent Anthropic API error occurred after one automatic retry. | Retry the request. If the error persists, check the Anthropic status page. |

---

## HTTP Status Codes

| Code | Meaning |
|------|---------|
| 200  | Success |
| 400  | Bad request - malformed input data |
| 422  | Unprocessable entity - user-fixable input problem (per RFC 9110 Section 15.5.21) |
| 500  | Internal server error - unexpected backend failure |

---

*Last updated: June 2026*
