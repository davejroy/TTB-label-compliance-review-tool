# Software Bill of Materials (SBOM)

This document lists the direct and resolved third-party dependencies for
the TTB Label Compliance Review Tool, generated from
`backend/requirements.txt` / the backend virtualenv, and
`frontend/package.json` / `frontend/package-lock.json`.

Regenerate with:

```bash
# Backend (run inside the activated venv)
pip freeze

# Frontend
cd frontend && npm list --depth=0
```

## Backend (Python, `backend/requirements.txt`)

| Package | Version | Direct/Transitive | Purpose |
| --- | --- | --- | --- |
| fastapi | 0.115.6 | Direct | Web framework / API routing |
| uvicorn (standard extras) | 0.34.0 | Direct | ASGI server |
| python-multipart | 0.0.20 | Direct | Multipart form/file upload parsing |
| anthropic | 0.45.2 | Direct | Anthropic SDK (Claude vision label extraction) |
| pydantic | 2.10.5 | Direct | Data validation / API schema models |
| python-dotenv | 1.0.1 | Direct | Loads `.env` for local development |
| annotated-types | 0.7.0 | Transitive | Pydantic dependency |
| anyio | 4.13.0 | Transitive | Async I/O abstraction (FastAPI/Starlette/httpx) |
| certifi | 2026.5.20 | Transitive | CA certificate bundle |
| click | 8.4.1 | Transitive | CLI toolkit (uvicorn) |
| distro | 1.9.0 | Transitive | OS detection (anthropic SDK) |
| h11 | 0.16.0 | Transitive | HTTP/1.1 protocol implementation |
| httpcore | 1.0.9 | Transitive | HTTP transport (httpx) |
| httptools | 0.8.0 | Transitive | HTTP parsing (uvicorn) |
| httpx | 0.28.1 | Transitive | HTTP client (anthropic SDK) |
| idna | 3.18 | Transitive | Internationalized domain name support |
| iniconfig | 2.3.0 | Transitive | pytest dependency |
| jiter | 0.15.0 | Transitive | Fast JSON parsing (anthropic SDK) |
| packaging | 26.2 | Transitive | Version/metadata utilities |
| pluggy | 1.6.0 | Transitive | pytest plugin system |
| pydantic_core | 2.27.2 | Transitive | Pydantic core (Rust) |
| Pygments | 2.20.0 | Transitive | Syntax highlighting (pytest output) |
| pytest | 9.0.3 | Dev/Test | Test runner |
| PyYAML | 6.0.3 | Transitive | YAML parsing (uvicorn `--reload`) |
| sniffio | 1.3.1 | Transitive | Async library detection |
| starlette | 0.41.3 | Transitive | ASGI toolkit underlying FastAPI |
| typing_extensions | 4.15.0 | Transitive | Backported typing features |
| uvloop | 0.22.1 | Transitive | High-performance event loop (uvicorn) |
| watchfiles | 1.2.0 | Transitive | File watching for `--reload` |
| websockets | 16.0 | Transitive | WebSocket support (uvicorn) |

## Frontend (Node, `frontend/package.json`)

| Package | Version | Type | Purpose |
| --- | --- | --- | --- |
| react | 19.2.7 | Dependency | UI library |
| react-dom | 19.2.7 | Dependency | React DOM renderer |
| @eslint/js | 10.0.1 | Dev | ESLint shared configs |
| @tailwindcss/vite | 4.3.0 | Dev | Tailwind CSS Vite plugin |
| @types/node | 24.13.2 | Dev | Node.js TypeScript types |
| @types/react | 19.2.17 | Dev | React TypeScript types |
| @types/react-dom | 19.2.3 | Dev | React DOM TypeScript types |
| @vitejs/plugin-react | 6.0.2 | Dev | Vite React plugin (Fast Refresh) |
| autoprefixer | 10.5.0 | Dev | CSS vendor prefixing |
| eslint | 10.4.1 | Dev | Linting |
| eslint-plugin-react-hooks | 7.1.1 | Dev | React Hooks lint rules |
| eslint-plugin-react-refresh | 0.5.2 | Dev | React Fast Refresh lint rules |
| globals | 17.6.0 | Dev | Global variable definitions for ESLint |
| postcss | 8.5.15 | Dev | CSS transformation pipeline |
| tailwindcss | 4.3.0 | Dev | Utility-first CSS framework |
| typescript | 6.0.3 | Dev | TypeScript compiler |
| typescript-eslint | 8.61.0 | Dev | TypeScript ESLint integration |
| vite | 8.0.16 | Dev | Frontend build tool / dev server |

Transitive frontend dependencies (tens of packages from the React, Vite,
ESLint, and TypeScript ecosystems) are pinned in
`frontend/package-lock.json`; run `npm list` for the full resolved tree.

## External services

| Service | Purpose | Data sent |
| --- | --- | --- |
| Anthropic API (`api.anthropic.com`) | Claude vision model used to transcribe label images into structured fields | Uploaded label image bytes, sent per-request and not persisted by this application |

## Notes

- This SBOM reflects dependency versions resolved at the time of writing.
  Run the regeneration commands above before each release to keep it
  current.
- License information for each package can be obtained with
  `pip-licenses` (backend) or `npx license-checker` (frontend).
