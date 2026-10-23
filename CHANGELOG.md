# Changelog

All notable changes to the TTB Label Compliance Review Tool are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) conventions.

---

## [Unreleased]

### Fixed
- Review routes catch `LowConfidenceError` from `run_compliance_checks` and return the retake-photo message on HTTP 200 (`overall_status=fail`, `error` set) instead of a bare HTTP 500. Covers `/api/review`, `/api/review/batch` (via `_review_single`), and `/api/review/batch/stream`.
- OCR mis-reading of hyphenated line breaks (e.g., CON-SUMPTION misread as DO NOT)
- Low-quality images now return user-friendly error instead of internal exception

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
