# Changelog

All notable changes to the TTB Label Compliance Review Tool are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) conventions.

---

## [Unreleased]

### Changed
- Sulfite citation follows beverage type: wine `27 CFR 4.32(e)`, distilled spirits `27 CFR 5.63(c)(7)`, malt beverages `27 CFR 7.63(b)(3)`. Missing sulfite is still `needs_verification` for wine only; beer and spirits auto-pass. Type-size still picks the most severe completed measurement; `cannot_measure` never outranks a completed result.
- Type-size: container-geometry estimates below 27 CFR 16.22 become `needs_verification`. A hard type-size fail still requires a calibrated scale-marker measurement.
- COLA name-and-address matching tolerates a missing or variant 27 CFR 4.35 / 5.66-5.68 / 7.66-7.68 bottler phrase when the remaining name and place match exactly.
- Printed `Imported by` overrides a domestic origin guess so country-of-origin cannot clean-pass.
- Missing wine sulfite declaration is `needs_verification` (27 CFR 4.32(e)).
- CSV Comments cite only the applicable CFR and strip a trailing period before the parenthetical.
- Extraction keeps the printed bottler phrase, title-cases ALL-CAPS class/type, and still omits `temperature` / `top_p` / `top_k` (API 2026-10-21 returns HTTP 400 if those are sent).

### Changed
- Vision/auth/timeout/rate-limit failures now return typed non-2xx bodies (`VISION_UNAVAILABLE` 503, `UPSTREAM_ERROR` 502, `UPSTREAM_TIMEOUT` 504, `RATE_LIMITED` 429) instead of HTTP 200 `overall_status=error`. Pre-fill never treats these as success.
- CSV export is one row per label with Status (`PASS` / `FAIL` / `NEEDS VERIFICATION` / `NOT REVIEWED`) and a Comments column. Every completed row is exported. Formula-injection prefixes (`=`, `+`, `-`, `@`, tab, CR) are neutralized. No `.xlsx` export.

### Fixed
- Streaming batch later-label exceptions keep the label index: `as_completed` wrappers no longer drop the error row.
- Street + city + state lines (`Main Street`, `Main St NW`, `St Charles Ave`, `PO Box 123`) stay addresses. Company names that happen to contain `St`/`Box`/`Way` (`Box Creek`, `St George Spirits`) still extract as the bottler. Numbered PO Box / route clauses that also carry an industry or legal word (`Box 12 Distilling`, `12 Apostles Wine Co`) stay the bottler. A numbered street that already ends in a street suffix (`123 Winery Lane`) or a trailing Colorado `CO` (`123 Main St, Denver, CO`) stays an address. Uncomma'd numbered companies whose name includes `Lane`/`Court`/`St` (`12 Lane Distilling CO`) stay the bottler.
- Streaming batch later-label `StructuredHTTPException` rows use the same user-message scrubber as other client errors (no raw provider/key text).
- Type-size 16.22 measurement uses the warning bbox from the matching `image_index` on multi-image submissions.
- Streaming batch review retries the same transient Claude failures as the single-label path and still fails cleanly per item.
- City + state abbreviations (`Denver CO`, `Colorado Springs CO`, `Fort Collins CO`) are not parsed as the bottler/importer company name. `Hopline Brewing Co` and `Fort Collins Brewing Co` stay companies.
- Merged per-photo `field_locations` keep `image_index` tied to the source photo after a retake; replacing a photo clears stale index-aligned results.
- Offline regression scorer no longer grades an empty/failed report as a pass and scores type-size findings.
- `wine_imported_pass` fixture now draws country of origin and sulfite text for a compliant imported wine.
- Blank/empty photos use `cause=blank` and blank-photo wording instead of glare.

### Security
- Pin `pillow-heif==1.8.1` (keep `Pillow==13.0.0`) so HEIC decode cannot stay on a pre-1.7.0 wheel. The frontend rejects `VITE_API_HOST` values that are not an optional `http(s)` scheme + `[A-Za-z0-9.-]` host + optional port + optional trailing slash (invalid values fall back to same-origin; a non-empty invalid host fails the Vite build). Vite `loadEnv` reads only `VITE_` variables. The CSP `<meta content>` value is HTML-attribute-escaped. Fail-open demo access and `USE_FAST_EXTRACTION` defaults are unchanged.
- Promoted streamed upload size cap + CORS on 413: the HTTP 413 request-body cap now counts bytes actually read (chunked, missing, or understated `Content-Length` can no longer bypass `MAX_REQUEST_BODY_BYTES`). The 413 is sent as soon as the cap is crossed (leftover chunks are not drained). If the cap cannot be read the limiter fails closed (413) instead of accepting an unlimited body. CORS wraps the size limiter so a 413 to an allowed origin carries `Access-Control-Allow-Origin` and the structured `{error_code, message}` body; disallowed origins still get no CORS headers. A wildcard origin is never paired with credentials. Security headers wrap CORS so preflight OPTIONS still get CSP/HSTS. The frontend unwraps single, batch, and stream wrappers and shows the structured FILE_TOO_LARGE text (never a proxy HTML 413 page). Demo-token gate (fail-open when unset) and other fail-open limiters are unchanged.
- Review follow-up: only `GET`/`HEAD` `/api/health` skip the body cap (POST/PUT on that path can no longer send an unlimited body). The limiter will not emit a second `http.response.start` if the inner app already began a response. Early 413s adopt a safe incoming `X-Request-ID`. CSV exports prefix `=`/`+`/`-`/`@` fields so untrusted vision/LLM text cannot become a spreadsheet formula. GitHub Actions are pinned to commit SHAs. Frontend nginx forwards `Host: $proxy_host` instead of the request Host header. Backend image uses `python:3.11.18-slim-bookworm`, `apt-get upgrade`, and drops pip/setuptools/wheel after install. Frontend `minimatch@11.0.1` override clears the remaining npm audit finding.
- CORS no longer defaults to `*`. Unset `CORS_ORIGINS` fails open to local Vite and the Render frontend from `render.yaml`; methods/headers are restricted and credentials stay off.
- `/docs`, `/redoc`, and `/openapi.json` use a docs CSP that allows FastAPI Swagger/ReDoc assets (`default-src 'none'` plus jsDelivr / FastAPI images, `connect-src 'self'`). API JSON routes keep `default-src 'none'; frame-ancestors 'none'`.
- Frontend `VITE_API_HOST` and CSP `connect-src` now share `resolveApiOrigin` so the configured API origin is always allowed. Docker nginx remains `connect-src 'self'` (same-origin).

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
