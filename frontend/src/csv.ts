/** Quote a CSV field if it contains a comma, quote, or newline, per RFC 4180. */
function escapeCsvField(value: string): string {
  if (/[",\n\r]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

/** Serialize rows of strings into CSV text using CRLF line endings. */
export function toCsv(rows: string[][]): string {
  return rows.map((row) => row.map(escapeCsvField).join(",")).join("\r\n");
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
