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
  if (
    isRetakeResult(result)
    || result.overall_status === "warning"
    || result.overall_status === "needs_verification"
  ) {
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

const FIELD_CITATIONS: Record<string, Record<string, string>> = {
  brand_name: {
    wine: "27 CFR 4.32(a) / 4.33",
    distilled_spirits: "27 CFR 5.63(a)(1) / 5.64",
    beer: "27 CFR 7.63(a)(1) / 7.64",
  },
  class_type: {
    wine: "27 CFR 4.32(a) / 4.34",
    distilled_spirits: "27 CFR 5.63(a)(2) / 5.141",
    beer: "27 CFR 7.63(a)(2) / 7.141",
  },
  alcohol_content: {
    wine: "27 CFR 4.36",
    distilled_spirits: "27 CFR 5.63(a)(3) / 5.65",
    beer: "27 CFR 7.63(a)(3) / 7.65",
  },
  net_contents: {
    wine: "27 CFR 4.37 / 4.72",
    distilled_spirits: "27 CFR 5.70 / 5.203",
    beer: "27 CFR 7.70",
  },
  name_and_address: {
    wine: "27 CFR 4.35",
    distilled_spirits: "27 CFR 5.66-5.68",
    beer: "27 CFR 7.66-7.68",
  },
  country_of_origin: {
    wine: "19 CFR 134.11",
    distilled_spirits: "19 CFR 134.11",
    beer: "19 CFR 134.11",
  },
  sulfite_declaration: {
    wine: "27 CFR 4.32(e)",
    distilled_spirits: "27 CFR 5.63(c)(7)",
    beer: "27 CFR 7.63(b)(3)",
  },
};

function beverageKey(beverageType: string): "wine" | "distilled_spirits" | "beer" {
  const key = beverageType.toLowerCase().replace(/[\s-]+/g, "_");
  if (key.includes("wine")) return "wine";
  if (key.includes("beer") || key.includes("malt")) return "beer";
  if (key.includes("spirit")) return "distilled_spirits";
  return "wine";
}

function uniqueCitations(items: Array<string | null | undefined>): string[] {
  return [...new Set(items.filter((item): item is string => Boolean(item)))];
}

function governmentWarningCitations(field: FieldResult): string[] {
  const failedFindings = (field.warning_format?.findings || []).filter((item) => item.status === "fail");
  const typeSize =
    field.type_size_details?.state === "fail"
    || field.type_size_details?.state === "needs_verification"
    || failedFindings.some((item) => item.code === "type_size");
  const bold = failedFindings.some((item) => item.code === "header_bold");
  const message = field.message || "";
  const textIssue =
    (field.warning_text_diff != null && !field.warning_text_diff.exact_match)
    || /not found on label/i.test(message)
    || /incorrect GOVERNMENT WARNING marker/i.test(message)
    || /header must appear in capital letters/i.test(message)
    || /does not match the required 27 CFR 16\.21/i.test(message);
  const cites: string[] = [];
  if (textIssue) cites.push("27 CFR 16.21");
  if (bold) cites.push("27 CFR 16.22(a)(2)");
  if (typeSize) cites.push("27 CFR 16.22(b)");
  return uniqueCitations(cites);
}

function citationsFor(field: FieldResult, beverageType: string): string[] {
  if (field.field.startsWith("government_warning")) {
    return governmentWarningCitations(field);
  }
  const mapped = FIELD_CITATIONS[field.field];
  if (!mapped) return [];
  const cite = mapped[beverageKey(beverageType)] || mapped.wine;
  return cite ? [cite] : [];
}

/** Append missing citations and strip a trailing period before the parenthetical. */
export function appendCitations(message: string, citations: string[]): string {
  const missing = citations.filter((cite) => cite && !message.includes(cite));
  if (missing.length === 0) return message;
  return `${message.replace(/[.]+$/, "").trimEnd()} (${missing.join(", ")}).`;
}

function failComment(field: FieldResult, beverageType: string): string {
  return `${field.label_name} failed: ${appendCitations(field.message.trim(), citationsFor(field, beverageType))}`;
}

/** Short plain-text explanation for the CSV Comments column. */
export function exportComment(
  result: ReviewResult | LabelCheckResult,
  beverageType = "",
): string {
  const status = exportStatus(result);
  if (status === "NOT REVIEWED") {
    const code = result.error_code || "INTERNAL_ERROR";
    return `${code}: The label was not checked.`;
  }
  if (status === "PASS") {
    const gw = findingsOf(result).find((field) => field.field.startsWith("government_warning"));
    const typeState = gw?.type_size_details?.state
      ?? gw?.warning_format?.findings.find((item) => item.code === "type_size")?.status;
    const typeOk = typeState === "pass"
      ? "warning text and type size OK"
      : "warning text OK; type size could not be confirmed from this photo and needs a manual check";
    return `All required elements present; ${typeOk}`;
  }

  const findings = findingsOf(result);
  const resolvedType =
    beverageType
    || ("beverage_type" in result && result.beverage_type
      ? String(result.beverage_type)
      : "");
  if (status === "FAIL") {
    const failed = findings.filter((field) => field.status === "fail");
    if (failed.length === 0) {
      return result.error || "A required label element failed compliance review.";
    }
    return failed.map((field) => failComment(field, resolvedType)).join(" ");
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

  const warnings = findings.filter(
    (field) => field.status === "warning" || field.status === "needs_verification",
  );
  if (warnings.length > 0) {
    return `Reviewer must confirm: ${warnings
      .map((field) => `${field.label_name} — ${appendCitations(field.message, citationsFor(field, resolvedType))}`)
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
    exportComment(result, beverageType),
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
