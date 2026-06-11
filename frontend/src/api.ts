import type { ApplicationData, ReviewResult } from "./types";

export async function reviewLabel(
  file: File,
  application: ApplicationData
): Promise<ReviewResult> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("application", JSON.stringify(application));

  const response = await fetch("/api/review", {
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

  const response = await fetch("/api/review/batch", {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Batch review failed (${response.status}): ${detail}`);
  }

  return response.json();
}
