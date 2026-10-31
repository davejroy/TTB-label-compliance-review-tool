# Security Review - OWASP Top 10 for LLM Applications (2025)

**Scope:** TTB Label Compliance Review Tool (backend). Review and
recommendations only -- no code changes are made by this document.
**Method:** static read of `app/auth.py`, `app/main.py`,
`app/claude_client.py`, `app/compliance.py`, `app/models.py` and the CI
workflow.
**Overall posture:** solid for a demo/pilot. The single most important
anti-injection control (forced structured tool output) is already in place,
secrets are env-only, uploads are size/count/signature validated, and every
review endpoint is behind an auth dependency. The main gaps are inbound rate
limiting, explicit output/PII handling, and logging/monitoring.

Severity legend: [HIGH] act before production, [MED] plan soon, [LOW] hardening.

---

## LLM01 - Prompt Injection

**Observed:** The Claude call forces a structured tool call
(`tool_choice={"type":"tool","name":"record_label_fields"}`, fixed
`system=SYSTEM_PROMPT`, untrusted image sent as `role:"user"` content).
Because the model must answer through the tool's `input_schema`, text such as
"ignore your instructions" printed on a label cannot easily redirect control
flow -- output is shape-constrained. This is the correct primary defense.

**Residual risk:** a malicious label could still try to influence *field
values* (e.g. printing fake ABV/warning text). That is inherent to OCR and is
mitigated downstream by deterministic compliance checks, not by the model.

**Recommendations:**
- [MED] Treat every extracted string as untrusted data end-to-end; never
  feed extracted text back into a subsequent prompt without the same tool
  constraint (avoids second-order injection).
- [LOW] Add a short system-prompt clause stating that text within the image
  is data to transcribe, never instructions to follow.
- [LOW] Keep a regression fixture with adversarial on-label text to prove the
  tool-output constraint holds across model upgrades.

## LLM02 - Sensitive Information Disclosure

**Observed:** No hardcoded secrets; API key, demo token, model name and CORS
origins all come from environment variables. Error strings reference env-var
*names*, not values.

**Recommendations:**
- [MED] Confirm label images and extracted text are not written to persistent
  logs; a label can contain a producer's name/address (PII).
- [MED] Add an explicit data-retention statement: are uploaded images stored,
  and for how long? Prefer in-memory-only processing where possible.
- [LOW] Scrub upstream Anthropic error payloads before surfacing them.

## LLM03 - Supply Chain

**Observed:** A `docs/SBOM.md` already exists. Dependencies are installed
from `requirements.txt`; CI caches on it.

**Recommendations:**
- [MED] Pin dependencies to hashes (pip-compile / hashes) and enable
  Dependabot + `pip-audit` in CI.
- [LOW] Pin the Claude model version explicitly and record it in the SBOM so
  behavior changes are traceable.

## LLM04 - Data and Model Poisoning

**Observed:** The app does not train or fine-tune; it calls a hosted model.
Poisoning surface is limited to the input image.

**Recommendations:**
- [LOW] The committed fixture corpus is a good guard against silent behavior
  drift; run the e2e job on model upgrades and diff extraction results.

## LLM05 - Improper Output Handling

**Observed:** Tool output is parsed as JSON against a schema; compliance runs
deterministically on typed fields. Results are returned as JSON via typed
FastAPI response models.

**Recommendations:**
- [MED] Ensure the frontend renders extracted strings as text, never as HTML
  (a label could contain `<script>`-like text) -- confirm React escaping /
  no `dangerouslySetInnerHTML`.
- [LOW] Validate/normalize extracted numeric fields (ABV, net contents) with
  bounds before display to avoid absurd values from a bad read.

## LLM06 - Excessive Agency

**Observed:** The model has exactly one tool (`record_label_fields`) that
only records data. It cannot call external systems, write files, or take
actions. Agency is appropriately minimal.

**Recommendations:**
- [LOW] Keep it this way: if future tools are added, gate any side-effecting
  tool behind explicit server-side authorization, not model discretion.

## LLM07 - System Prompt Leakage

**Observed:** The system prompt describes extraction only and contains no
secrets. Leakage would be low impact.

**Recommendations:**
- [LOW] Never place credentials, tokens, or internal hostnames in the system
  prompt (currently satisfied).

## LLM08 - Vector / Embedding Weaknesses

**Not applicable:** no RAG, vector store, or embeddings in the current design.
Re-evaluate if retrieval is added later.

## LLM09 - Misinformation / Over-reliance

**Observed:** Compliance pass/fail is deterministic (27 CFR rules), not the
model's opinion; the model only transcribes. A confidence gate already
rejects low-confidence reads. This is the right separation.

**Recommendations:**
- [MED] Surface a clear disclaimer that results are a decision-support aid,
  not a legal determination, and show per-field confidence to the reviewer.
- [LOW] Log confidence distributions to spot model degradation over time.

## LLM10 - Unbounded Consumption (cost / DoS)

**Observed:** `MAX_FILE_SIZE=10MB`, `MAX_IMAGES_PER_LABEL=4`, `max_tokens=1500`,
magic-byte validation before I/O, and upstream 429/529 retry. Good cost
guards. **Gap:** no inbound per-client rate limiting on the app's own
endpoints, so a holder of the shared demo token could drive API spend.

**Recommendations:**
- [HIGH] Add inbound rate limiting (e.g. slowapi / reverse-proxy) per token /
  IP on `/api/review*` and `/api/label-check*`.
- [MED] Add a global daily spend cap / circuit breaker on Anthropic usage.
- [LOW] Bound total request payload size (sum of images), not just per-image.

---

## Cross-cutting (API / application security)

- [HIGH] The current gate is a single shared demo token (constant-time
  compared via `hmac.compare_digest` -- good). Before production, move to real
  per-user auth (OAuth/OIDC) with revocation; document that the demo gate is
  not user authentication (the code already says so).
- [MED] CORS defaults to `"*"` when `CORS_ORIGINS` is unset; require an
  explicit allow-list in production and fail closed if unset.
- [MED] Add structured request logging with correlation IDs and basic
  anomaly/abuse monitoring (ties to LLM10).
- [MED] Add security headers (HSTS, X-Content-Type-Options, CSP) at the edge.
- [LOW] Enforce HTTPS-only and secure cookie/though token transport at the
  proxy.
- [LOW] Add `pip-audit` and secret-scanning (gitleaks) as CI steps.

## Update 2026-10-31 — vision error paths (LLM05 / info leak)

Typed vision failures (`VISION_UNAVAILABLE` 503, `UPSTREAM_ERROR` 502,
`UPSTREAM_TIMEOUT` 504, `RATE_LIMITED` 429) return `{error_code, message,
request_id}` only. Messages are photo-is-fine copy. Exception types are
logged by name; stacks, keys, and env values are not returned. Health
exposes `vision_configured` as a boolean. CSV Comments for `NOT REVIEWED`
include the error code, not the exception. Formula-injection prefixes in
CSV cells are prefixed with `'`. React still renders extracted strings as
text (no `dangerouslySetInnerHTML`).

## Priority summary

1. [HIGH] Inbound rate limiting + real auth before production.
2. [MED] Confirm no PII (images/extracted text) in logs; set retention.
3. [MED] Dependency pinning + pip-audit + spend cap.
4. [MED] Confirm frontend escapes extracted text; require CORS allow-list.
5. [LOW] Adversarial-label regression fixture; security headers; secret scan.
