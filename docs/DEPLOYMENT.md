# Deployment Guide

This document describes how the TTB Label Compliance Review Tool is built and
deployed. The canonical source of truth is `render.yaml` at the repository root;
this guide explains it and the operational steps around it.

Related documents: [ALWAYS_ON_HOSTING.md](./ALWAYS_ON_HOSTING.md), [CONFIGURATION.md](./CONFIGURATION.md),
[TECHNICAL_ARCHITECTURE.md](./TECHNICAL_ARCHITECTURE.md),
[ERROR_CODES.md](./ERROR_CODES.md), [HANDOFF.md](./HANDOFF.md).

## Platform

The application is deployed on Render.com as two services defined in
`render.yaml`:

1. Backend web service (`type: web`, `runtime: python`)
2. Frontend static site (`type: static`, `runtime: static`)

**Instance Tier & Always-On Hosting:** Production uses Render's Starter instance tier for always-on uptime (eliminating 30–60s idle spin-down cold starts; external keep-alive pingers are paused/archived). The development/testing environment uses Render's free tier.

## Backend Service

| Setting | Value |
|---|---|
| Root directory | backend |
| Python version | 3.11.10 |
| Plan | free |
| Build command | pip install -r requirements.txt |
| Start command | uvicorn app.main:app --host 0.0.0.0 --port $PORT |
| ASGI app | app.main:app (FastAPI, title "TTB Label Compliance Review Tool") |

Backend environment variables (see [CONFIGURATION.md](./CONFIGURATION.md) for
full semantics):

- ANTHROPIC_API_KEY - set manually in the Render dashboard (sync: false); never committed.
- CLAUDE_MODEL - set to claude-sonnet-4-6 in deployment (the in-code default is claude-sonnet-4-5).
- CORS_ORIGINS - allowed browser origins for the frontend.
- DEMO_ACCESS_TOKEN - set manually in the dashboard (sync: false); enables the access gate.
- DEMO_USERNAME - ttb-demo.

## Frontend Service

| Setting | Value |
|---|---|
| Root directory | frontend |
| Build command | npm install && npm run build |
| Publish path | dist |
| SPA routing | rewrite /* -> /index.html |
| API host env | VITE_API_HOST -> ttb-label-backend.onrender.com |

## Promotion Flow (dev to production)

1. Make changes on a branch and open a pull request against `main`.
2. Run backend pytest test suite and frontend vitest suite along with TypeScript build and ESLint checks.
3. Merging/pushing to `main` is the production promotion; Render redeploys from `main`.

## Secrets Handling

All secrets (ANTHROPIC_API_KEY, DEMO_ACCESS_TOKEN) use `sync: false` in
`render.yaml`, meaning they are entered directly in the Render dashboard and are
never stored in the repository. Rotating a secret is done in the dashboard and
does not require a code change.

## Rollback

Use Render's deploy history to roll back the affected service to a previous
successful deploy. Because configuration lives in `render.yaml` and secrets live
in the dashboard, a code rollback restores prior behavior without secret changes.
