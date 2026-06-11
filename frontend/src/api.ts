import type { ApplicationData, ReviewResult } from "./types";

// In production the frontend and backend are deployed as separate Render
// services, so the backend's hostname is baked in at build time via
// VITE_API_HOST. In local dev this is left empty and Vite's dev server proxy
// (see vite.config.ts) forwards /api requests to the local backend.
const API_BASE = import.meta.env.VITE_API_HOST
  ? `https://${import.meta.env.VITE_API_HOST}`
  : "";

export async function reviewLabel(
  file: File,
  application: ApplicationData
): Promise<ReviewResult> {
  const formData = new FormData();
  formData.append("file", file);
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

export async function reviewLabelsBatch(
  items: { file: File; application: ApplicationData }[]
): Promise<ReviewResult[]> {
  const formData = new FormData();
  items.forEach((item) => formData.append("files", item.file));
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
