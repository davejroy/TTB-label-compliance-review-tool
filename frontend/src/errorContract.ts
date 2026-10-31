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

const GENERIC_413_MESSAGE =
  "This upload is too large. Please use fewer photos or smaller files (each photo can be up to 10 MB).";

// Unwraps every API wrapper that throwApiError prefixes, including
// "Batch review failed (413):" and "Batch stream failed (413):".
const STATUS_LINE_PREFIX =
  /^(?:[\w][\w /-]*failed|Failed to [^:]+?) \(\d+\):\s*/i;

const ACTIONABLE_413_MESSAGE =
  /^(?:The upload is too large\.|This upload is too large\.|The template file is too large\.|File '.+' exceeds 10 MB limit\.)/i;

function actionableUploadSizeMessage(fallback: string): string | null {
  const cleaned = fallback.replace(STATUS_LINE_PREFIX, "").trim();
  if (!cleaned || /<[a-z!/]/i.test(cleaned) || /request entity too large/i.test(cleaned)) {
    return null;
  }
  return ACTIONABLE_413_MESSAGE.test(cleaned) ? cleaned : null;
}

export function httpStatusUserMessage(
  status: number,
  fallback: string,
  errorCode?: ErrorCode,
): string {
  if (status === 401) {
    return "Your session is not authorized. Please sign in again and retry.";
  }
  if (status === 413) {
    // Prefer the structured API FILE_TOO_LARGE text. Never surface proxy HTML
    // ("413 Request Entity Too Large") or a "Review failed (413):" wrapper.
    if (errorCode === "FILE_TOO_LARGE" || !errorCode) {
      return actionableUploadSizeMessage(fallback) ?? GENERIC_413_MESSAGE;
    }
    return GENERIC_413_MESSAGE;
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

export const PANEL_ORDER: RetakeArea[] = ["front", "back", "neck"];

export function defaultPanelRole(index: number): RetakeArea {
  return PANEL_ORDER[index] ?? "front";
}

export function nextUnusedPanelRole(used: Array<RetakeArea | undefined>): RetakeArea {
  const taken = new Set(used.filter((role): role is RetakeArea => !!role));
  return PANEL_ORDER.find((role) => !taken.has(role)) ?? "front";
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
