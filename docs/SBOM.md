# Software Bill of Materials (SBOM)

## Purpose
This document provides an inventory of software components, dependency versions, security reviews, and external services for the TTB Label Compliance Review Tool.

## SBOM Summary

| Field | Value |
|---|---|
| Last Updated | 2026-09-16 |
| Tool Used | pip freeze, npm list |
| SBOM Format | Markdown Inventory (aligned with CycloneDX/SPDX component models) |
| Scope | Full Repository (Backend + Frontend) |
| Generated Artifact | `docs/SBOM.md` |

---

## Dependency Ecosystems

| Ecosystem | Manifest File | Lock File | Package Manager |
|---|---|---|---|
| Python (Backend) | `backend/requirements.txt` | `backend/requirements.txt` | pip |
| JavaScript/TypeScript (Frontend) | `frontend/package.json` | `frontend/package-lock.json` | npm |

---

## Direct Dependencies

### Backend (Python, `backend/requirements.txt`)

| Name | Version | Ecosystem | Purpose | License | Source |
|---|---|---|---|---|---|
| fastapi | 0.115.6 | PyPI | Web framework / API routing | MIT | PyPI |
| uvicorn | 0.34.0 | PyPI | ASGI server | BSD-3-Clause | PyPI |
| python-multipart | 0.0.20 | PyPI | Multipart form/file upload parsing | Apache-2.0 | PyPI |
| anthropic | 0.45.2 | PyPI | Anthropic SDK (Claude vision label extraction) | MIT | PyPI |
| pydantic | 2.10.5 | PyPI | Data validation / API schema models | MIT | PyPI |
| python-dotenv | 1.0.1 | PyPI | Loads `.env` for local development | BSD-3-Clause | PyPI |
| Pillow | 11.1.0 | PyPI | Image decoding and OCR preprocessing | HPND | PyPI |
| slowapi | 0.1.9 | PyPI | Soft rate limiting and abuse guardrails | MIT | PyPI |
| annotated-types | 0.7.0 | Transitive | Pydantic dependency | MIT | PyPI |

### Frontend (Node, `frontend/package.json`)

| Name | Version | Ecosystem | Purpose | License | Source |
|---|---|---|---|---|---|
| react | 19.2.7 | npm | UI library | MIT | npm |
| react-dom | 19.2.7 | npm | React DOM renderer | MIT | npm |
| vite | 8.0.16 | npm | Frontend build tool / dev server | MIT | npm |
| tailwindcss | 4.3.0 | npm | Utility-first CSS framework | MIT | npm |
| typescript | 6.0.3 | npm | TypeScript compiler | Apache-2.0 | npm |

---

## Security Review

| Dependency | Version | Finding | Severity | Status | Notes |
|---|---|---|---|---|---|
| anthropic | 0.45.2 | No known CVEs | Low | Monitored | Vision API integration |
| fastapi / starlette | 0.115.6 | No known CVEs | Low | Monitored | Hardened with security headers middleware |
| glob (frontend dev) | 10.5.0 | Deprecation note | Low | Evaluated | Dev-only tooling dependency |

---

## License Review

| Dependency | License | Concern | Action |
|---|---|---|---|
| All direct dependencies | MIT / BSD / Apache-2.0 / HPND | None (permissive open-source licenses) | Permitted for federal & enterprise use |

---

## External Services

| Service | Purpose | Data sent |
| --- | --- | --- |
| Anthropic API (`api.anthropic.com`) | Claude vision model used to transcribe label images into structured fields | Uploaded label image bytes, sent per-request and not persisted by this application |

---

## SBOM Generation Commands

```bash
# Backend
cd backend && pip freeze

# Frontend
cd frontend && npm list --depth=0
```

## Known Limitations
- Transitive dependencies are locked in `frontend/package-lock.json`. Full transitive graphs can be extracted with CycloneDX CLI if machine-readable XML/JSON artifacts are requested.

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-09-16 | Updated with safe hardening components and governance standard formatting | Kilroy_Lives |
