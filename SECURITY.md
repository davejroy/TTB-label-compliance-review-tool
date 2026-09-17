# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| main    | ✅ Yes     |

## Reporting a Vulnerability

If you discover a security vulnerability in this project, **do not open a public GitHub Issue**.

Please report it privately by emailing the repository owner via GitHub's private vulnerability reporting feature:

1. Navigate to the **Security** tab of this repository.
2. Click **"Report a vulnerability"**.
3. Provide a clear description of the issue, including steps to reproduce.

We aim to acknowledge reports within **5 business days** and to release a fix or mitigation within **30 days** for confirmed vulnerabilities.

## Scope

This tool processes uploaded beverage label images and calls the Anthropic Claude API. The following are considered in-scope for security reports:

- **Prompt injection via label images** — malicious text embedded in uploaded images that could alter the compliance analysis.
- **API key exposure** — any path by which the `ANTHROPIC_API_KEY` environment variable could be leaked to end users or logs.
- **File upload abuse** — bypassing the 4-image-per-label limit, the 10 MB file size limit, or the MIME type checks to cause denial-of-service or unexpected behavior.
- **Sensitive data in responses** — any case where internal error details (stack traces, file paths) are returned to API consumers.

## Out of Scope

- Vulnerabilities in third-party libraries (report those to the upstream project).
- Theoretical issues with no practical exploit path.

## Security Practices

- Images are processed entirely in memory and are never written to disk (per NFR-2 in `docs/PDR.md`).
- Pillow `Image.MAX_IMAGE_PIXELS` is explicitly capped at 64,000,000 to defend against image decompression bomb exploits.
- Inbound rate limiting is applied via `slowapi` on expensive review endpoints, returning HTTP 429 Too Many Requests (never 401/403) with Retry-After guidance while keeping `/api/health` and `/api/demo-info` unrestricted.
- Process-wide concurrency limits (`asyncio.Semaphore`, configurable via `MAX_CONCURRENT_CLAUDE_CALLS`) wrap Claude API vision extractions to prevent API resource exhaustion.
- The Anthropic API key is read from the environment and is never echoed in API responses or logs.
- User-facing error messages do not include internal exception details or stack traces (masked in streaming batch outputs).
- Fail-open authentication design ensures public evaluators are never locked out: `DEMO_ACCESS_TOKEN` is unset by default and no mandatory `API_AUTH_TOKEN` is required.
- CORS origins are configurable via the `CORS_ORIGINS` environment variable and default fail-open to `*` for local development and public evaluators.

## Disclosure Policy

We follow **coordinated disclosure**: we will work with you to understand and remediate the issue before any public disclosure.
