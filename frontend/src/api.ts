import type { ApplicationData, LabelCheckResult, ReviewResult } from "./types";

// In production the frontend and backend are deployed as separate Render
// services, so the backend's hostname is baked in at build time via
// VITE_API_HOST. In local dev this is left empty and Vite's dev server proxy
// (see vite.config.ts) forwards /api requests to the local backend.
const API_BASE = import.meta.env.VITE_API_HOST
  ? `https://${import.meta.env.VITE_API_HOST}`
    : "";

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
          // not JSON – fall through to raw text
    }
    return text;
}

/**
 * Wrap a fetch call and convert low-level network errors (no connection,
 * DNS failure, etc.) into a friendlier message before re-throwing.
 */
async function safeFetch(url: string, init: RequestInit): Promise<Response> {
    try {
          return await fetch(url, init);
    } catch (err) {
          if (err instanceof TypeError) {
                  throw new Error(
                            "Could not reach the review server. Check your connection or try again in a moment."
                          );
          }
          throw err;
    }
}

/** POST /api/review - Single Review mode: one label's image(s) + application data. */
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

/**
 * POST /api/review/batch - Batch Review mode: each item is one label's
 * image(s) + application data. Files are flattened into a single form field
 * with a parallel `image_counts` array so the backend can split them back
 * into per-label groups.
 */
export async function reviewLabelsBatch(
    items: { files: File[]; application: ApplicationData }[]
  ): Promise<ReviewResult[]> {
    const formData = new FormData();
    items.forEach((item) => item.files.forEach((file) => formData.append("files", file)));
    formData.append("image_counts", JSON.stringify(items.map((item) => item.files.length)));
    formData.append(
          "applications",
          JSON.stringify(items.map((item) => item.application))
        );

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

/**
 * POST /api/label-check/batch - Label-Only Check mode: each item is one
 * label's image(s), with no application data required.
 */
export async function checkLabelsBatch(
    items: { files: File[] }[]
  ): Promise<LabelCheckResult[]> {
    const formData = new FormData();
    items.forEach((item) => item.files.forEach((file) => formData.append("files", file)));
    formData.append("image_counts", JSON.stringify(items.map((item) => item.files.length)));

  const response = await safeFetch(`${API_BASE}/api/label-check/batch`, {
        method: "POST",
        body: formData,
  });

  if (!response.ok) {
        const detail = await parseErrorBody(response);
        throw new Error(`Label check failed (${response.status}): ${detail}`);
  }

  return response.json() as Promise<LabelCheckResult[]>;
}
