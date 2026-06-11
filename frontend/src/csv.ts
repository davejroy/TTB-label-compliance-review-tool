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
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
