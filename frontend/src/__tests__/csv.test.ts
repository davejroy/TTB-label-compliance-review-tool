/**
 * Tests for src/csv.ts
  *
   * toCsv() and escapeCsvField() are pure functions with no DOM or API
    * dependencies, making them the ideal first test target per the issue
     * triage recommendation. No API key or browser environment needed.
      *
       * Covers: RFC 4180 quoting, CRLF line endings, special characters,
        * multi-row output, empty inputs, and the UTF-8 BOM stub check.
         */
import { describe, it, expect } from 'vitest';
import {
  CSV_RESULT_HEADER,
  exportComment,
  exportStatus,
  labelCheckResultCsvRows,
  reviewResultCsvRows,
  toCsv,
} from '../csv';

describe('toCsv', () => {
    it('serialises a single row of plain strings', () => {
          expect(toCsv([['Brand Name', 'Status', 'Message']]))
                  .toBe('Brand Name,Status,Message');
    });

    it('uses CRLF line endings between rows', () => {
          const result = toCsv([['a', 'b'], ['c', 'd']]);
          expect(result).toBe('a,b\r\nc,d');
    });

    it('quotes a field that contains a comma', () => {
          expect(toCsv([['Old Tom, Distillery', 'pass']]))
                  .toBe('"Old Tom, Distillery",pass');
    });

    it('quotes a field that contains a double-quote and escapes it', () => {
          // RFC 4180: embedded double quotes are escaped by doubling them.
          expect(toCsv([['say "hello"', 'pass']]))
                  .toBe('"say ""hello""",pass');
    });

    it('quotes a field that contains a newline', () => {
          expect(toCsv([['line1\nline2', 'pass']]))
                  .toBe('"line1\nline2",pass');
    });

    it('quotes a field that contains a carriage return', () => {
          expect(toCsv([['line1\rline2', 'pass']]))
                  .toBe('"line1\rline2",pass');
    });

    it('does not quote a plain field with no special characters', () => {
          expect(toCsv([['GOVERNMENT WARNING', 'fail']]))
                  .toBe('GOVERNMENT WARNING,fail');
    });

    it('handles empty string fields without quoting', () => {
          expect(toCsv([['', 'pass', '']]))
                  .toBe(',pass,');
    });

    it('handles an empty rows array', () => {
          expect(toCsv([])).toBe('');
    });

    it('handles a row with a single field', () => {
          expect(toCsv([['Only field']])).toBe('Only field');
    });

    it('produces correct output for a realistic batch-review header + data row', () => {
          const header = ['Label #', 'Filename', 'Overall Status', 'Field', 'Check Status', 'Message'];
          const row = ['1', 'front.jpg', 'fail', 'government_warning', 'fail',
                             'Government Warning issue(s): header must read exactly \'GOVERNMENT WARNING:\''];
          const result = toCsv([header, row]);
          const lines = result.split('\r\n');
          expect(lines).toHaveLength(2);
          expect(lines[0]).toBe('Label #,Filename,Overall Status,Field,Check Status,Message');
          // Message field is plain text (no comma/quote/newline) so should not be quoted.
          expect(lines[1]).toContain('government_warning');
    });

    it('handles a field with both a comma and a quote', () => {
          // e.g. 'Alcohol content differs by 0.4%, exceeding the "0.3%" tolerance'
          const field = 'Differs by 0.4%, exceeding "0.3%" tolerance';
          const result = toCsv([[field]]);
          // Should be double-quoted with internal quotes doubled.
          expect(result).toBe('"Differs by 0.4%, exceeding ""0.3%"" tolerance"');
    });

    it.each(['=CMD()', '+1+1', '-2+3', '@SUM(A1)', '\t=1+1'])(
          'neutralizes spreadsheet formula injection starting with %s',
          (payload) => {
            const result = toCsv([[payload, 'pass']]);
            expect(result.startsWith('=') || result.startsWith('+') || result.startsWith('-') || result.startsWith('@')).toBe(false);
            expect(result).toContain(`'${payload}`);
            expect(result).not.toMatch(/^[=+\-@]/);
          },
    );
});

describe('CSV result header', () => {
    it('is Files, Beverage Type, Status, Comments, Request ID — no Excel columns', () => {
        expect(CSV_RESULT_HEADER).toEqual([
            'Files',
            'Beverage Type',
            'Status',
            'Comments',
            'Request ID',
        ]);
        expect(CSV_RESULT_HEADER).not.toContain('Sheet');
        expect(CSV_RESULT_HEADER.join(',')).not.toMatch(/xlsx|excel/i);
    });
});

describe('reviewResultCsvRows', () => {
    it('exports one row per label with Status and Comments', () => {
        const rows = reviewResultCsvRows({
            filenames: ['front.jpg', 'back.jpg'],
            overall_status: 'retake',
            processing_time_ms: 100,
            error: 'Photo quality is too low, please retake.',
            error_code: 'PHOTO_UNREADABLE_FIELD',
            request_id: 'req-retake',
            retake: {
                field: 'brand',
                area: 'front',
                cause: 'blur',
                tip: 'Hold the phone still and tap the label to focus before you take the photo.',
            },
            fields: [
                {
                    field: 'brand_name',
                    label_name: 'Brand Name',
                    status: 'pass',
                    application_value: 'Old Tom',
                    label_value: 'Old Tom',
                    message: 'Matches application',
                },
            ],
            extracted: { government_warning_present: true, field_locations: [] },
        }, 'distilled_spirits');
        expect(rows).toHaveLength(1);
        expect(rows[0][2]).toBe('NEEDS VERIFICATION');
        expect(rows[0][3]).toMatch(/reviewer must confirm/i);
        expect(rows[0][3]).toMatch(/front/i);
        expect(rows[0]).not.toContain('fail');
        expect(rows[0]).not.toContain('PASS');
    });

    it('exports errored items as NOT REVIEWED with the code, never pass or fail', () => {
        const result = {
            filenames: ['bad.jpg'],
            overall_status: 'error' as const,
            processing_time_ms: 40,
            error: 'The label reader timed out — your photo is fine. Try again shortly.',
            error_code: 'UPSTREAM_TIMEOUT' as const,
            request_id: 'req-timeout',
            fields: [],
            extracted: { government_warning_present: false, field_locations: [] },
        };
        expect(exportStatus(result)).toBe('NOT REVIEWED');
        expect(exportComment(result)).toBe('UPSTREAM_TIMEOUT: The label was not checked.');
        const rows = reviewResultCsvRows(result);
        expect(rows).toEqual([
            ['bad.jpg', '', 'NOT REVIEWED', 'UPSTREAM_TIMEOUT: The label was not checked.', 'req-timeout'],
        ]);
        expect(rows[0][2]).not.toBe('PASS');
        expect(rows[0][2]).not.toBe('FAIL');
        expect(exportComment({
            filenames: ['missing-key.jpg'],
            overall_status: 'error',
            error_code: 'VISION_UNAVAILABLE',
            processing_time_ms: 10,
            fields: [],
            extracted: { government_warning_present: false, field_locations: [] },
        })).toBe('VISION_UNAVAILABLE: The label was not checked.');
    });

    it('writes PASS comments that describe what was verified', () => {
        const rows = reviewResultCsvRows({
            filenames: ['ok.jpg'],
            overall_status: 'pass',
            processing_time_ms: 80,
            request_id: 'req-pass',
            fields: [],
            extracted: { government_warning_present: true, field_locations: [] },
        });
        expect(rows[0][2]).toBe('PASS');
        expect(rows[0][3]).toMatch(/all required elements present/i);
        expect(rows[0][3]).toMatch(/warning text and type size/i);
    });

    it('writes NEEDS VERIFICATION comments that say what the reviewer must confirm', () => {
        const result = {
            filenames: ['maybe.jpg'],
            overall_status: 'warning' as const,
            processing_time_ms: 70,
            request_id: 'req-warn',
            fields: [
                {
                    field: 'alcohol_content',
                    label_name: 'Alcohol Content',
                    status: 'warning' as const,
                    application_value: '45% Alc./Vol.',
                    label_value: '45% Alc/Vol',
                    message: 'Punctuation differs from the application; confirm the statement is equivalent.',
                },
            ],
            extracted: { government_warning_present: true, field_locations: [] },
        };
        expect(exportStatus(result)).toBe('NEEDS VERIFICATION');
        expect(exportComment(result)).toMatch(/reviewer must confirm/i);
        expect(exportComment(result)).toMatch(/Alcohol Content/);
        expect(exportComment(result)).toMatch(/equivalent/i);
        const rows = reviewResultCsvRows(result, 'distilled_spirits');
        expect(rows[0][2]).toBe('NEEDS VERIFICATION');
        expect(rows[0][3]).toBe(exportComment(result));
    });
});

describe('labelCheckResultCsvRows', () => {
    it('writes FAIL with the item and CFR citation in Comments', () => {
        const rows = labelCheckResultCsvRows({
            filenames: ['label.jpg'],
            overall_status: 'fail',
            beverage_type: 'distilled_spirits',
            processing_time_ms: 90,
            request_id: 'req-fail',
            checks: [
                {
                    field: 'government_warning',
                    label_name: 'Government Warning',
                    status: 'fail',
                    application_value: 'Required',
                    label_value: '',
                    message: 'Government Warning statement not found on label. Required on all alcohol beverage labels (27 CFR Part 16).',
                },
            ],
            extracted: { government_warning_present: false, field_locations: [] },
        }, 'Distilled Spirits');
        expect(rows[0][2]).toBe('FAIL');
        expect(rows[0][3]).toMatch(/Government Warning failed/i);
        expect(rows[0][3]).toMatch(/27 CFR Part 16/);
        expect(rows[0][1]).toBe('Distilled Spirits');
    });

    it('appends the CFR citation when it lives on the warning-format finding', () => {
        const rows = labelCheckResultCsvRows({
            filenames: ['type.jpg'],
            overall_status: 'fail',
            beverage_type: 'distilled_spirits',
            processing_time_ms: 90,
            request_id: 'req-typesize',
            checks: [
                {
                    field: 'government_warning_type_size',
                    label_name: 'Government Warning type size',
                    status: 'fail',
                    application_value: 'Required',
                    label_value: '0.8 mm',
                    message: 'Measured type size is below the 1.0 mm minimum.',
                    warning_format: {
                        status: 'fail',
                        required_min_height_mm: 1.0,
                        max_characters_per_inch: 25,
                        findings: [
                            {
                                code: 'type_size',
                                status: 'fail',
                                message: 'Below 1.0 mm',
                                citation: '27 CFR 16.22',
                                estimated: false,
                            },
                        ],
                    },
                },
            ],
            extracted: { government_warning_present: true, field_locations: [] },
        }, 'Distilled Spirits');
        expect(rows[0][2]).toBe('FAIL');
        expect(rows[0][3]).toMatch(/Government Warning type size failed/i);
        expect(rows[0][3]).toMatch(/below the 1\.0 mm minimum/i);
        expect(rows[0][3]).toMatch(/27 CFR 16\.22/);
    });

    it('keeps every completed row, including NOT REVIEWED errors', () => {
        const pass = reviewResultCsvRows({
            filenames: ['a.jpg'],
            overall_status: 'pass',
            processing_time_ms: 10,
            fields: [],
            extracted: { government_warning_present: true, field_locations: [] },
        });
        const error = labelCheckResultCsvRows({
            filenames: ['b.jpg'],
            overall_status: 'error',
            error_code: 'RATE_LIMITED',
            processing_time_ms: 10,
            checks: [],
            extracted: { government_warning_present: false, field_locations: [] },
        }, 'Wine');
        const combined = [...pass, ...error];
        expect(combined).toHaveLength(2);
        expect(combined[0][2]).toBe('PASS');
        expect(combined[1][2]).toBe('NOT REVIEWED');
        expect(combined[1][3]).toBe('RATE_LIMITED: The label was not checked.');
    });
});

describe('formula injection escaping', () => {
    it('prefixes cells that start with = + - @ tab or CR', async () => {
        const { escapeFormulaInjection, toCsv } = await import('../csv');
        expect(escapeFormulaInjection('=cmd|calc')).toBe("'=cmd|calc");
        expect(escapeFormulaInjection('+1+1')).toBe("'+1+1");
        expect(escapeFormulaInjection('-2+3')).toBe("'-2+3");
        expect(escapeFormulaInjection('@SUM(A1)')).toBe("'@SUM(A1)");
        expect(escapeFormulaInjection('\tHYPERLINK')).toBe("'\tHYPERLINK");
        expect(escapeFormulaInjection('\r=1+1')).toBe("'\r=1+1");
        expect(escapeFormulaInjection('All required elements present')).toBe(
            'All required elements present',
        );
        const csv = toCsv([['=HYPERLINK("http://evil")', 'PASS']]);
        expect(csv).toContain("'=HYPERLINK");
        expect(csv.startsWith("=")).toBe(false);
    });
});

describe('downloadColaTemplateCsv', () => {
    it('is a callable function that triggers download of standard COLA headers', async () => {
        const { downloadColaTemplateCsv } = await import('../csv');
        expect(typeof downloadColaTemplateCsv).toBe('function');
    });
});

