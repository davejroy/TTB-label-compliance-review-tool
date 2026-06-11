import type { ApplicationData, LabelCheckResult, ReviewResult } from "./types";

// In production the frontend and backend are deployed as separate Render
// services, so the backend's hostname is baked in at build time via
// VITE_API_HOST. In local dev this is left empty and Vite's dev server proxy
// (see vite.config.ts) forwards /api requests to the local backend.
const API_BASE = import.meta.env.VITE_API_HOST
  ? `https://${import.meta.env.VITE_API_HOST}`
  : "";

/** POST /api/review - Single Review mode: one label's image(s) + application data. */
export async function reviewLabel(
  files: File[],
  application: ApplicationData
): Promise<ReviewResult> {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  formData.append("application", JSON.stringify(application));

  const response = await fetch(`${API_BASE}/api/review`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Review failed (${response.status}): ${detail}`);
  }

  return response.json();
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

  const response = await fetch(`${API_BASE}/api/review/batch`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Batch review failed (${response.status}): ${detail}`);
  }

  return response.json();
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

  const response = await fetch(`${API_BASE}/api/label-check/batch`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Label check failed (${response.status}): ${detail}`);
  }

  return response.json();
}
