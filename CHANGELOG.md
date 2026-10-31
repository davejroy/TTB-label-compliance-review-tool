# Changelog

All notable changes to the TTB Label Compliance Review Tool are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) conventions.

---

## [Unreleased]

### Security
- Promoted `-dev` `7b69489` (#49): the HTTP 413 request-body cap now counts bytes actually read (chunked, missing, or understated `Content-Length` can no longer bypass `MAX_REQUEST_BODY_BYTES`). The 413 is sent as soon as the cap is crossed (leftover chunks are not drained). If the cap cannot be read the limiter fails closed (413) instead of accepting an unlimited body. CORS wraps the size limiter so a 413 to an allowed origin carries `Access-Control-Allow-Origin` and the structured `{error_code, message}` body; disallowed origins still get no CORS headers. A wildcard origin is never paired with credentials. Security headers wrap CORS so preflight OPTIONS still get CSP/HSTS. The frontend unwraps single, batch, and stream wrappers and shows the structured FILE_TOO_LARGE text (never a proxy HTML 413 page). Demo-token gate (fail-open when unset) and other fail-open limiters are unchanged.
- CORS no longer defaults to `*`. Unset `CORS_ORIGINS` fails open to local Vite and the Render frontend from `render.yaml`; methods/headers are restricted and credentials stay off (port of -dev 91e88ff).
- Promoted `-dev` `ceb18a8` security pins (#50/#51): `fastapi==0.162.0`, `starlette==1.10.0`, `Pillow==13.0.0`, `python-dotenv==1.2.4`; frontend `vite@8.3.5`, `vitest@5.0.5`, `happy-dom@21.0.4`, `zod@4.6.7` override.

### Added
- SECURITY.md: vulnerability disclosure policy
- CONTRIBUTING.md: developer setup and code standards
- docs/ERROR_CODES.md: user-facing error message reference
- docs/REGULATORY_REFERENCES.md: TTB/27 CFR citation index
- CHANGELOG.md: this file

### Changed
- backend/app/main.py: Enforce 4-image-per-label limit with user-friendly error message
- backend/app/main.py: Fix indentation in _read_and_validate_file and _read_images
- backend/app/claude_client.py: Add ImageQualityError exception, module logger, minimum pixel check
- backend/app/compliance.py: Add module logger for audit-trail logging
- backend/app/compliance.py: Fix _normalize_whitespace to rejoin hyphenated line breaks

### Fixed
- Type-size image-index regression no longer asserts threadpool call order; bbox is keyed by filename.
- Production `tsc -b` excludes Vitest files so Vite 8 / Vitest 5 matcher types do not fail the frontend bundle; Vite config imports `cspPolicy.ts` with an extension.
- OCR mis-reading of hyphenated line breaks (e.g., CON-SUMPTION misread as DO NOT)
- Low-quality images now return user-friendly error instead of internal exception

---

## [v1.0.0] - 2025-06

### Added
- Initial release
- FastAPI backend with /api/review, /api/review/batch, /api/label-check/batch endpoints
- Claude vision integration for label field extraction
- Government Warning check (27 CFR 16.20)
- Alcohol content, net contents, country of origin, brand name checks
- 10 MB per-image file size limit
- Frontend React application

---

*Versions follow [Semantic Versioning](https://semver.org/).* 
