import { ApiError } from "../api";
import type { ErrorCode } from "../types";

export const PNG_BYTES = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x01,
]);

export const VISION_FAILURES: Array<{
  code: ErrorCode;
  status: number;
  message: string;
}> = [
  {
    code: "VISION_UNAVAILABLE",
    status: 503,
    message: "The label reader is unavailable right now — your photo is fine. Try again shortly.",
  },
  {
    code: "UPSTREAM_ERROR",
    status: 502,
    message: "The label reader is unavailable right now — your photo is fine. Try again shortly.",
  },
  {
    code: "UPSTREAM_TIMEOUT",
    status: 504,
    message: "The label reader timed out — your photo is fine. Try again shortly.",
  },
  {
    code: "RATE_LIMITED",
    status: 429,
    message: "The label reader is busy — your photo is fine. Wait a moment and try again.",
  },
];

export function visionApiError(failure: (typeof VISION_FAILURES)[number]): ApiError {
  return new ApiError(failure.message, failure.status, failure.code, `req-${failure.code}`);
}

export function pngFile(name = "front.png"): File {
  return new File([PNG_BYTES], name, { type: "image/png" });
}
