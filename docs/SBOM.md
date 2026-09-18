# Software Bill of Materials (SBOM)

## Purpose

This document provides a comprehensive inventory of direct and resolved third-party dependencies, license classifications, and security audit findings for the TTB Label Compliance Review Tool.

## SBOM Summary

| Field | Value |
|---|---|
| Last Updated | 2026-09-18 |
| Tool Used | pip-audit, npm audit, pip freeze, npm list |
| SBOM Format | Manual Markdown Catalog aligned with CycloneDX / SPDX structures |
| Scope | Full Repository (Backend Python Web Service + Frontend React/TypeScript SPA) |
| Generated Artifact | `backend/requirements.txt`, `frontend/package-lock.json` |

## Dependency Ecosystems

| Ecosystem | Manifest File | Lock File | Package Manager | Runtime Environment |
|---|---|---|---|---|
| Python (Backend) | `backend/requirements.txt` | N/A (Pinned Manifest) | pip | Python 3.11 / 3.12 (uvicorn ASGI) |
| JavaScript/TypeScript (Frontend) | `frontend/package.json` | `frontend/package-lock.json` | npm | Node.js 20+ (Vite SPA) |

## Direct Dependencies

### Backend (`backend/requirements.txt`)

| Name | Version | Ecosystem | Purpose | License | Source |
|---|---|---|---|---|---|
| `fastapi` | 0.115.6 | Python | Web framework / REST API routing | MIT | PyPI |
| `uvicorn[standard]` | 0.34.0 | Python | ASGI production server | BSD-3-Clause | PyPI |
| `python-multipart` | 0.0.32 | Python | Multipart form / file upload parsing | Apache-2.0 | PyPI |
| `anthropic` | 0.45.2 | Python | Anthropic Claude Vision API SDK | MIT | PyPI |
| `pydantic` | 2.10.5 | Python | Data validation & schema serialization | MIT | PyPI |
| `python-dotenv` | 1.0.1 | Python | Local `.env` environment loader | BSD-3-Clause | PyPI |
| `Pillow` | 11.1.0 | Python | Image decoding, bounds checking, preprocessing | MIT-CMU | PyPI |
| `opencv-python-headless` | 5.0.0.93 | Python | Scale-marker detection, homography, CV type-size measurement (27 CFR 16.22 Option A) | Apache-2.0 | PyPI |
| `slowapi` | 0.1.9 | Python | Inbound soft rate limiting & abuse prevention | MIT | PyPI |

### Frontend (`frontend/package.json`)

| Name | Version | Ecosystem | Purpose | License | Source |
|---|---|---|---|---|---|
| `react` | 19.2.7 | npm | UI component library | MIT | npm |
| `react-dom` | 19.2.7 | npm | React DOM rendering engine | MIT | npm |
| `vite` | 8.0.16 | npm (dev) | Frontend build tool & local dev server | MIT | npm |
| `tailwindcss` | 4.3.0 | npm (dev) | Utility-first styling framework | MIT | npm |
| `@tailwindcss/vite` | 4.3.0 | npm (dev) | Tailwind Vite integration plugin | MIT | npm |
| `typescript` | ~6.0.3 | npm (dev) | Static type checker | Apache-2.0 | npm |
| `vitest` | 3.2.7 | npm (dev) | Unit / component test runner | MIT | npm |
| `happy-dom` | 18.0.1 | npm (dev) | Headless DOM test environment | MIT | npm |
| `eslint` | 10.4.1 | npm (dev) | Code quality linter | MIT | npm |

## Security Review & Vulnerability Audit (September 2026)

| Dependency | Ecosystem | Version | Finding / Advisory | Severity | Status | Remediations / Rationale |
|---|---|---|---|---|---|---|
| `python-multipart` | Python | 0.0.20 -> 0.0.32 | PYSEC-2026-1852, PYSEC-2026-3036..3040 (Multipart boundary parsing vulnerabilities) | Moderate | **Fixed** | Updated pin in `backend/requirements.txt` to `0.0.32`. Validated against all 210 pytest test cases. |
| `starlette` | Python (transitive) | 0.41.3 | PYSEC-2026-1941, PYSEC-2026-1942, PYSEC-2026-161 (Denial of service via malformed multipart/range headers) | Moderate | **Deferred** | Pinned transitively by `fastapi==0.115.6`. Upgrading to Starlette 1.x requires major FastAPI framework upgrade. Risk mitigated by `slowapi` rate limits, in-memory payload limits (`MAX_FILE_SIZE = 10MB`), and Pillow image pixel caps. |
| `Pillow` | Python | 11.1.0 | PYSEC-2026-2249, PYSEC-2026-2250 (Buffer overflows in obscure/unsupported raw image decoders) | Moderate | **Deferred** | Major upgrade to Pillow 12.x requires validation across all CV and OpenCV pipeline functions. In-app risk is mitigated by magic-byte format validation (`_reject_if_not_image`), strictly allowing only JPEG/PNG formats, and `Image.MAX_IMAGE_PIXELS = 64_000_000`. |
| `happy-dom` | npm (dev) | 18.0.1 | GHSA-37j7-fg3j-429f, GHSA-w4gp-fjgq-3q4g (VM context escape in test runner) | Critical (in VM contexts) | **Deferred** | DevDependency only (used solely inside local Vitest execution). Fix requires breaking major update to `happy-dom@20.x`. Zero impact on production static web bundle. |
| `@vitest/mocker` / `vitest` | npm (dev) | 3.2.7 | GHSA-82fw-gwwq-j7x9 (Redirect mock path traversal in test runner) | Moderate | **Deferred** | DevDependency only (test execution). Fix requires breaking major upgrade to `vitest@5.x`. Zero impact on production bundle. |
| Transitive npm packages | npm | Various | Minor transitive vulnerabilities | Low | **Fixed** | Applied safe `npm audit fix` updating non-breaking transitive locks in `frontend/package-lock.json`. All 77 Vitest tests pass cleanly. |

## License Review

| License | Components | Compatibility Notes |
|---|---|---|
| MIT | `fastapi`, `anthropic`, `pydantic`, `slowapi`, `react`, `react-dom`, `vite`, `tailwindcss`, `vitest`, `eslint` | Permissive; fully compatible with open/commercial deployment. |
| BSD-3-Clause | `uvicorn`, `python-dotenv` | Permissive; attribution retained. |
| Apache-2.0 | `python-multipart`, `opencv-python-headless`, `typescript` | Permissive; patent grant included; compatible. |
| MIT-CMU | `Pillow` | Permissive; historical PIL license. |

## External Services & Egress Points

| Service | Endpoint | Purpose | Data Transmitted | Egress Security Controls |
|---|---|---|---|---|
| Anthropic API | `https://api.anthropic.com/v1/messages` | Claude Vision label transcription | Preprocessed label image bytes (base64) + structured prompt | Process-wide concurrency semaphore (`MAX_CONCURRENT_CLAUDE_CALLS=5`), TLS 1.3 encryption, API key stored only in backend env |

## SBOM Generation Commands

```bash
# Python Backend Dependencies
cd backend
python3 -m pip freeze > requirements.lock
python3 -m pip_audit --local

# Frontend Node Dependencies
cd frontend
npm list --depth=0
npm audit
```

## Known Limitations

1. **Static Pinning**: Backend `requirements.txt` pins top-level direct packages; transitive pinning is managed through standard virtual environment builds.
2. **Stateless Operations**: No runtime package downloads or dynamic plugin loading occurs during execution.

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-07-07 | Initial SBOM authored | davejroy |
| 2026-09-09 | Updated transitive dependency pins and added latency logging dependencies | Kilroy_Lives |
| 2026-09-16 | Updated OpenCV and SlowAPI dependencies | Kilroy_Lives |
| 2026-09-18 | Conducted dependency security audit (`pip-audit`, `npm audit`), bumped `python-multipart` to `0.0.32`, applied `npm audit fix`, and documented CVE review rationale | Kilroy_Lives |
