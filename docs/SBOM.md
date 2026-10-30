# Software Bill of Materials (SBOM)

## Purpose

This document provides a comprehensive inventory of direct and resolved third-party dependencies, license classifications, and security audit findings for the TTB Label Compliance Review Tool.

## SBOM Summary

| Field | Value |
|---|---|
| Last Updated | 2026-10-30 |
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
| `fastapi` | 0.162.0 | Python | Web framework / REST API routing | MIT | PyPI |
| `starlette` | 1.10.0 | Python | ASGI toolkit (direct pin; FastAPI 0.162.0 requires `>=0.46.0`) | BSD-3-Clause | PyPI |
| `uvicorn[standard]` | 0.34.0 | Python | ASGI production server | BSD-3-Clause | PyPI |
| `python-multipart` | 0.0.32 | Python | Multipart form / file upload parsing | Apache-2.0 | PyPI |
| `anthropic` | 2.3.0 | Python | Anthropic Claude Vision API SDK | MIT | PyPI |
| `pydantic` | 2.10.5 | Python | Data validation & schema serialization | MIT | PyPI |
| `python-dotenv` | 1.2.4 | Python | Local `.env` environment loader | BSD-3-Clause | PyPI |
| `Pillow` | 13.0.0 | Python | Image decoding, bounds checking, preprocessing | MIT-CMU | PyPI |
| `opencv-python-headless` | 5.0.0.93 | Python | Scale-marker detection, homography, CV type-size measurement (27 CFR 16.22 Option A) | Apache-2.0 | PyPI |
| `slowapi` | 0.1.9 | Python | Inbound soft rate limiting & abuse prevention | MIT | PyPI |

### Frontend (`frontend/package.json`)

| Name | Version | Ecosystem | Purpose | License | Source |
|---|---|---|---|---|---|
| `react` | 19.2.7 | npm | UI component library | MIT | npm |
| `react-dom` | 19.2.7 | npm | React DOM rendering engine | MIT | npm |
| `vite` | 8.3.5 | npm (dev) | Frontend build tool & local dev server | MIT | npm |
| `tailwindcss` | 4.3.0 | npm (dev) | Utility-first styling framework | MIT | npm |
| `@tailwindcss/vite` | 4.3.0 | npm (dev) | Tailwind Vite integration plugin | MIT | npm |
| `typescript` | ~6.0.3 | npm (dev) | Static type checker | Apache-2.0 | npm |
| `vitest` | 5.0.5 | npm (dev) | Unit / component test runner | MIT | npm |
| `happy-dom` | 21.0.4 | npm (dev) | Headless DOM test environment | MIT | npm |
| `zod` (override) | 4.6.7 | npm (dev, transitive) | Schema validation used by build tooling | MIT | npm |
| `eslint` | 10.4.1 | npm (dev) | Code quality linter | MIT | npm |

## Security Review & Vulnerability Audit (September 2026)

| Dependency | Ecosystem | Version | Finding / Advisory | Severity | Status | Remediations / Rationale |
|---|---|---|---|---|---|---|
| `python-multipart` | Python | 0.0.20 -> 0.0.32 | PYSEC-2026-1852, PYSEC-2026-3036..3040 (Multipart boundary parsing vulnerabilities) | Moderate | **Fixed** | Updated pin in `backend/requirements.txt` to `0.0.32`. Validated against all 210 pytest test cases. |
| `starlette` | Python | 0.41.3 -> **1.10.0** | PYSEC-2026-1941/1942/161 (multipart/range DoS) | Moderate | **Fixed** | Direct pin `starlette==1.10.0` with `fastapi==0.162.0` (promoted from `-dev` #51 `ceb18a8`). |
| `Pillow` | Python | 11.1.0 -> **13.0.0** | PYSEC-2026-2249/2250 and later decoder CVEs | Moderate | **Fixed** | Promoted from `-dev` #50 `35a2189`. Magic-byte allowlist, 10 MB per-file / 50 MB body caps, and `Image.MAX_IMAGE_PIXELS = 64_000_000` remain. |
| `happy-dom` | npm (dev) | 18.0.1 -> **21.0.4** | GHSA-37j7-fg3j-429f, GHSA-w4gp-fjgq-3q4g | Critical (test VM) | **Fixed** | Promoted from `-dev` #51. DevDependency only. |
| `vitest` | npm (dev) | 3.2.7 -> **5.0.5** | GHSA-82fw-gwwq-j7x9 | Moderate | **Fixed** | Promoted from `-dev` #51 with `vite@8.3.5`. |
| Transitive npm packages | npm | Various | Minor transitive vulnerabilities | Low | **Fixed** | `zod` override `4.6.7`; lockfile regenerated on `ceb18a8` pins. |
| `python-dotenv` | Python | 1.0.1 -> **1.2.4** | PYSEC-2026-2270 | Low–Moderate | **Fixed** | Promoted from `-dev` #50. Local `.env` loader only. |
| Production frontend (`npm audit --omit=dev`) | npm | runtime | none | — | **Clean** | 0 vulnerabilities in production dependencies (React 19). |
| `vite` / `tinypool` / `zod` | npm (dev) | 8.3.5 / 5.x / 4.6.7 | prior GHSA-9jrq-w75r-8gcw, GHSA-5gmw-xhrv-c9v3, GHSA-mrhx-c7xc-m3w5 | High/Critical (dev) | **Fixed** | Promoted from `-dev` #51 (`vite@8.3.5`, `vitest@5.0.5`, `zod@4.6.7` override). |
| Gradio / Streamlit demo pins | Python (dev-only) | 6.31.0 / 1.66.1 | n/a | — | **Residual (layout)** | Present on `-dev` #50 only. Production promote skips `tools/gradio_demo` and `tools/eval_dashboard`. |

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
| 2026-07-07 | Initial SBOM authored | Maintainer |
| 2026-09-09 | Updated transitive dependency pins and added latency logging dependencies | Maintainer |
| 2026-09-16 | Updated OpenCV and SlowAPI dependencies | Maintainer |
| 2026-09-18 | Conducted dependency security audit (`pip-audit`, `npm audit`), bumped `python-multipart` to `0.0.32`, applied `npm audit fix`, and documented CVE review rationale | Maintainer |
| 2026-10-30 | Re-ran pip-audit / npm audit / semgrep / bandit / gitleaks for PR #26. No dependency pins changed. Documented remaining Pillow/dotenv/dev-tool findings. | Maintainer |
| 2026-10-30 | Promoted `-dev` `ceb18a8` (#50/#51) security pins. Residual: Gradio/Streamlit demo trees are not in the production layout. | Maintainer |
| 2026-10-30 | Cleared remaining Bandit lows: env-var name composed in code; B311 nosec on non-crypto retry jitter. | Maintainer |
