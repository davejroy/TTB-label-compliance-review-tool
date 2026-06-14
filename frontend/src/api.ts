import type { ApplicationData, LabelCheckResult, ReviewResult } from "./types";

// In production the frontend and backend are deployed as separate Render
// services, so the backend's hostname is baked in at build time via
// VITE_API_HOST. In local dev this is left empty and Vite's dev server proxy
// forwards /api requests to the local backend.
const API_BASE = import.meta.env.VITE_API_HOST
  ? `https://${import.meta.env.VITE_API_HOST}`
  : "";

// Transient HTTP status codes that are worth retrying once.
// 429 = rate limited, 529 = Anthropic overloaded, 503 = service unavailable.
const RETRYABLE_STATUS = new Set([429, 503, 529]);
const RETRY_DELAY_MS = 2000;

/** Pause for `ms` milliseconds. */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Parse an error response body into a human-readable string.
 * FastAPI returns JSON `{"detail": "..."}` for validation errors;
 * other errors may be plain text. Falls back to the HTTP status line.
 */
async function parseErrorBody(response: Response): Promise<string> {
  const text = await response.text().catch(() => "");
  if (!text) return `HTTP ${response.status} ${response.statusText}`;
  try {
    const body = JSON.parse(text) as unknown;
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      return typeof detail === "string" ? detail : JSON.stringify(detail);
    }
  } catch {
    // not JSON - fall through to raw text
  }
  return text;
}

/**
 * Wrap a fetch call with retry logic for transient errors.
 * - Network errors (TypeError) are not retried - they indicate a connection
 *   problem that is unlikely to resolve in 2 seconds.
 * - Retryable HTTP status codes (429, 503, 529) are retried once after a
 *   short delay so transient API blips resolve transparently.
 */
async function safeFetch(url: string, init: RequestInit): Promise<Response> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 2; attempt++) {
    if (attempt > 0) await sleep(RETRY_DELAY_MS);
    let response: Response;
    try {
      response = await fetch(url, init);
    } catch (err) {
      if (err instanceof TypeError) {
        throw new Error(
          "Could not reach the review server. Check your connection or try again in a moment."
        );
      }
      throw err;
    }
    // Return immediately on success or non-retryable errors.
    if (response.ok || !RETRYABLE_STATUS.has(response.status)) {
      return response;
    }
    // Retryable status on first attempt - save and loop.
    lastError = response;
    if (attempt === 0) continue;
  }
  // Both attempts failed with a retryable status - return the last response.
  return lastError as Response;
}

/** POST /api/review - Single Review mode. */
export async function reviewLabel(
  files: File[],
  application: ApplicationData
): Promise<ReviewResult> {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  formData.append("application", JSON.stringify(application));

  const response = await safeFetch(`${API_BASE}/api/review`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const detail = await parseErrorBody(response);
    throw new Error(`Review failed (${response.status}): ${detail}`);
  }
  return response.json() as Promise<ReviewResult>;
}

/** POST /api/review/batch - Batch Review mode. */
export async function reviewLabelsBatch(
  items: { files: File[]; application: ApplicationData }[]
): Promise<ReviewResult[]> {
  const formData = new FormData();
  items.forEach((item) => item.files.forEach((file) => formData.append("files", file)));
  formData.append("image_counts", JSON.stringify(items.map((item) => item.files.length)));
  formData.append("applications", JSON.stringify(items.map((item) => item.application)));

  const response = await safeFetch(`${API_BASE}/api/review/batch`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const detail = await parseErrorBody(response);
    throw new Error(`Batch review failed (${response.status}): ${detail}`);
  }
  return response.json() as Promise<ReviewResult[]>;
}

/** POST /api/label-check/batch - Label-Only Check mode. */
export async function checkLabelsBatch(
  items: { files: File[]; photoRoles?: string[] }[],
  confirmedBeverageType?: string
): Promise<LabelCheckResult[]> {
  const formData = new FormData();
  const counts: number[] = [];
  const allRoles: string[] = [];
  let hasRoles = false;

  items.forEach((item) => {
    counts.push(item.files.length);
    item.files.forEach((file) => formData.append("files", file));
    if (item.photoRoles && item.photoRoles.length === item.files.length) {
      item.photoRoles.forEach((r) => allRoles.push(r));
      hasRoles = true;
    } else {
      item.files.forEach(() => allRoles.push(""));
    }
  });

  formData.append("image_counts", JSON.stringify(counts));
  if (confirmedBeverageType) {
    formData.append("confirmed_beverage_type", confirmedBeverageType);
  }
  if (hasRoles) {
    formData.append("photo_roles", JSON.stringify(allRoles));
  }

  const res = await safeFetch(`${API_BASE}/api/label-check/batch`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

/**
 * Ping the backend health endpoint and wait for it to respond.
 * On Render free tier the server spins down after 15 minutes of inactivity;
 * the first request after a spin-down can take 10-30 seconds to respond.
 * Returns "warm" if the server responded quickly (<3 s) or "cold" if it
 * needed to wake up. Throws if the server cannot be reached at all.
 */
export async function wakeServerIfNeeded(): Promise<"warm" | "cold"> {
  const WARM_THRESHOLD_MS = 3000;
  const WAKE_TIMEOUT_MS = 35000;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), WAKE_TIMEOUT_MS);
  const t0 = Date.now();
  try {
    const res = await fetch(`${API_BASE}/api/health`, {
      method: "GET",
      signal: controller.signal,
    });
    clearTimeout(timer);
    if (!res.ok) throw new Error(`Health check returned ${res.status}`);
    return Date.now() - t0 < WARM_THRESHOLD_MS ? "warm" : "cold";
  } catch (err) {
    clearTimeout(timer);
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error("Could not reach the review server. Check your connection or try again in a moment.");
    }
    if (err instanceof TypeError) {
      throw new Error("Could not reach the review server. Check your connection or try again in a moment.");
    }
    throw err;
  }
}
