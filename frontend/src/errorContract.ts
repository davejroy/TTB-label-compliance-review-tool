import type { ErrorCode, LabelCheckResult, RetakeArea, ReviewResult, Status } from "./types";

export const RETAKE_MESSAGE = "Photo quality is too low, please retake.";

export const RETAKE_CODES = new Set<ErrorCode>([
  "PHOTO_QUALITY_LOW",
  "PHOTO_UNREADABLE_FIELD",
]);

export function isRetakeCode(code?: ErrorCode | null): boolean {
  return !!code && RETAKE_CODES.has(code);
}

export function isRetakeResult(result: Pick<ReviewResult, "overall_status" | "error_code" | "retake">): boolean {
  return result.overall_status === "retake" || isRetakeCode(result.error_code) || !!result.retake;
}

export function isErrorResult(result: Pick<ReviewResult, "overall_status" | "error_code">): boolean {
  if (result.overall_status === "error") return true;
  return !!result.error_code && !isRetakeCode(result.error_code);
}

export function isFailResult(result: Pick<ReviewResult | LabelCheckResult, "overall_status">): boolean {
  return result.overall_status === "fail";
}

export function httpStatusUserMessage(status: number, fallback: string): string {
  if (status === 401) {
    return "Your session is not authorized. Please sign in again and retry.";
  }
  if (status === 413) {
    return "This upload is too large. Please use fewer photos or smaller files (each photo can be up to 10 MB).";
  }
  if (status === 429) {
    return "Too many requests. Please wait a moment and try again.";
  }
  return fallback;
}

export function statusLabel(status: Status): string {
  switch (status) {
    case "pass":
      return "Pass";
    case "warning":
      return "Needs Review";
    case "fail":
      return "Fail";
    case "retake":
      return "Retake";
    case "error":
      return "Error";
    default:
      return status;
  }
}

const PANEL_ORDER: RetakeArea[] = ["front", "back", "neck"];

export function defaultPanelRole(index: number): RetakeArea {
  return PANEL_ORDER[index] ?? "front";
}

export function areaToFileIndex(
  area: RetakeArea | undefined,
  fileCount: number,
  roles?: Array<RetakeArea | undefined>,
): number {
  if (fileCount <= 0) return 0;
  if (area && roles?.length) {
    const match = roles.findIndex((role) => role === area);
    if (match >= 0) return match;
  }
  if (area === "back") return Math.min(1, fileCount - 1);
  if (area === "neck") return fileCount - 1;
  return 0;
}
