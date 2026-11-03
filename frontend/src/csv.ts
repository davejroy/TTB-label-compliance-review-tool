import { isErrorResult, isRetakeResult } from "./errorContract";
import type { FieldResult, LabelCheckResult, ReviewResult } from "./types";

/** Quote a CSV field if it contains a comma, quote, or newline, per RFC 4180. */
function escapeCsvField(value: string): string {
  // Neutralize Excel/LibreOffice formula injection. Extracted label text and
  // application fields are untrusted (including vision/LLM output) and must
  // not become an active formula when an evaluator opens the CSV.
  let out = value;
  if (/^[=+\-@\t\r]/.test(out)) {
    out = `'${out}`;
  }
  if (/[",\n\r]/.test(out)) {
    return `"${out.replace(/"/g, '""')}"`;
  }
  return out;
}

/**
 * Neutralize spreadsheet formula injection. Excel, Sheets, and LibreOffice
 * treat a leading ``=``, ``+``, ``-``, ``@``, tab, or CR as a formula.
 * Prefix those cells with a single quote so they stay plain text.
 */
export function escapeFormulaInjection(value: string): string {
  if (!value) return value;
  const first = value[0];
  if (
    first === "=" ||
    first === "+" ||
    first === "-" ||
    first === "@" ||
    first === "\t" ||
    first === "\r"
  ) {
    return `'${value}`;
  }
  return value;
}

/** Serialize rows of strings into CSV text using CRLF line endings. */
export function toCsv(rows: string[][]): string {
  return rows
    .map((row) => row.map((cell) => escapeCsvField(escapeFormulaInjection(cell))).join(","))
    .join("\r\n");
}

/**
 * Trigger a browser download of `rows` as a CSV file named `filename`.
 *
 * Prepends a UTF-8 BOM so Excel opens the file with the correct encoding,
 * and cleans up the temporary object URL/anchor element afterwards.
 */
export function downloadCsv(filename: string, rows: string[][]): void {
  const csv = toCsv(rows);
  const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/**
 * Trigger a browser download of a sample CSV template for COLA application imports.
 */
export function downloadColaTemplateCsv(): void {
  const templateRows = [
    [
      "beverage_type",
      "brand_name",
      "class_type",
      "alcohol_content",
      "net_contents",
      "name_and_address",
      "country_of_origin",
    ],
    [
      "distilled_spirits",
      "Old Tom Distillery",
      "Kentucky Straight Bourbon Whiskey",
      "45% Alc./Vol. (90 Proof)",
      "750 mL",
      "Old Tom Distillery, Bardstown, KY",
      "",
    ],
    [
      "wine",
      "Silver Oak Cellars",
      "Cabernet Sauvignon",
      "14.2% Alc./Vol.",
      "750 mL",
      "Silver Oak Cellars, Oakville, CA",
      "",
    ],
    [
      "beer",
      "Hop Valley Brewing Co.",
      "India Pale Ale",
      "6.8% Alc./Vol.",
      "12 FL OZ",
      "Hop Valley Brewing Co., Eugene, OR",
      "",
    ],
    [
      "distilled_spirits",
      "Highland Glen",
      "Single Malt Scotch Whisky",
      "43% Alc./Vol.",
      "700 mL",
      "Imported by Glen Import Co., New York, NY",
      "Product of Scotland",
    ],
  ];
  downloadCsv("cola-application-template.csv", templateRows);
}

export const CSV_RESULT_HEADER = [
  "Files",
  "Beverage Type",
  "Status",
  "Comments",
  "Request ID",
] as const;

export type ExportStatus = "PASS" | "FAIL" | "NEEDS VERIFICATION" | "NOT REVIEWED";

export function exportStatus(
  result: Pick<ReviewResult, "overall_status" | "error_code" | "retake">,
): ExportStatus {
  if (isRetakeResult(result) || result.overall_status === "warning") {
    return "NEEDS VERIFICATION";
  }
  if (isErrorResult(result)) {
    return "NOT REVIEWED";
  }
  if (result.overall_status === "fail") {
    return "FAIL";
  }
  if (result.overall_status === "pass") {
    return "PASS";
  }
  return "NEEDS VERIFICATION";
}

function findingsOf(result: ReviewResult | LabelCheckResult): FieldResult[] {
  if ("fields" in result && Array.isArray(result.fields)) {
    return result.fields;
  }
  if ("checks" in result && Array.isArray(result.checks)) {
    return result.checks;
  }
  return [];
}

function citationFor(field: FieldResult): string | null {
  const fromFormat = field.warning_format?.findings.find((item) => item.citation)?.citation;
  return fromFormat || null;
}

function failComment(field: FieldResult): string {
  const citation = citationFor(field);
  const why = field.message.trim();
  if (citation && !why.includes(citation)) {
    return `${field.label_name} failed: ${why} (${citation}).`;
  }
  return `${field.label_name} failed: ${why}`;
}

/** Short plain-text explanation for the CSV Comments column. */
export function exportComment(result: ReviewResult | LabelCheckResult): string {
  const status = exportStatus(result);
  if (status === "NOT REVIEWED") {
    const code = result.error_code || "INTERNAL_ERROR";
    return `${code}: The label was not checked.`;
  }
  if (status === "PASS") {
    return "All required elements present; warning text and type size OK";
  }

  const findings = findingsOf(result);
  if (status === "FAIL") {
    const failed = findings.filter((field) => field.status === "fail");
    if (failed.length === 0) {
      return result.error || "A required label element failed compliance review.";
    }
    return failed.map(failComment).join(" ");
  }

  if (isRetakeResult(result)) {
    const parts = ["Reviewer must confirm the photo can be read."];
    if (result.retake?.field) {
      parts.push(`Unread field: ${result.retake.field}.`);
    }
    if (result.retake?.area) {
      parts.push(`Retake the ${result.retake.area} panel.`);
    }
    if (result.retake?.tip) {
      parts.push(result.retake.tip);
    } else if (result.error) {
      parts.push(result.error);
    }
    return parts.join(" ");
  }

  const warnings = findings.filter((field) => field.status === "warning");
  if (warnings.length > 0) {
    return `Reviewer must confirm: ${warnings
      .map((field) => `${field.label_name} — ${field.message}`)
      .join(" ")}`;
  }
  return result.error || "Reviewer must confirm this label before accepting the result.";
}

function resultCsvRow(
  result: ReviewResult | LabelCheckResult,
  beverageType: string,
): string[] {
  return [
    result.filenames.join("; "),
    beverageType,
    exportStatus(result),
    exportComment(result),
    result.request_id || "",
  ];
}

/** One CSV data row per reviewed label. Errors stay NOT REVIEWED, never pass/fail. */
export function reviewResultCsvRows(
  result: ReviewResult,
  beverageType = "",
): string[][] {
  return [resultCsvRow(result, beverageType)];
}

/** One CSV data row per label-check result. Errors stay NOT REVIEWED, never pass/fail. */
export function labelCheckResultCsvRows(
  result: LabelCheckResult,
  beverageType: string,
): string[][] {
  return [resultCsvRow(result, beverageType)];
}
