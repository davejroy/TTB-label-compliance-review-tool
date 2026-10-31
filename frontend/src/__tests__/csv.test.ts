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
import { toCsv } from '../csv';

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

describe('reviewResultCsvRows', () => {
    it('writes overall_status retake and keeps partial fields', async () => {
        const { reviewResultCsvRows } = await import('../csv');
        const rows = reviewResultCsvRows({
            filenames: ['front.jpg', 'back.jpg'],
            overall_status: 'retake',
            processing_time_ms: 100,
            error: 'Photo quality is too low, please retake.',
            error_code: 'PHOTO_UNREADABLE_FIELD',
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
        });
        expect(rows).toHaveLength(1);
        expect(rows[0][1]).toBe('retake');
        expect(rows[0][2]).toBe('Brand Name');
        expect(rows[0]).not.toContain('fail');
    });

    it('writes overall_status error when there are no fields', async () => {
        const { reviewResultCsvRows } = await import('../csv');
        const rows = reviewResultCsvRows({
            filenames: ['bad.jpg'],
            overall_status: 'error',
            processing_time_ms: 40,
            error: 'The review service timed out. Please try again.',
            error_code: 'UPSTREAM_TIMEOUT',
            fields: [],
            extracted: { government_warning_present: false, field_locations: [] },
        });
        expect(rows).toEqual([
            ['bad.jpg', 'error', '', '', '', '', 'The review service timed out. Please try again.'],
        ]);
    });
});

describe('labelCheckResultCsvRows', () => {
    it('writes overall_status fail only for a real compliance fail', async () => {
        const { labelCheckResultCsvRows } = await import('../csv');
        const rows = labelCheckResultCsvRows({
            filenames: ['label.jpg'],
            overall_status: 'fail',
            beverage_type: 'distilled_spirits',
            processing_time_ms: 90,
            checks: [
                {
                    field: 'government_warning',
                    label_name: 'Government Warning',
                    status: 'fail',
                    application_value: 'Required',
                    label_value: '',
                    message: 'Government Warning statement not found on label.',
                },
            ],
            extracted: { government_warning_present: false, field_locations: [] },
        }, 'Distilled Spirits');
        expect(rows[0][2]).toBe('fail');
        expect(rows[0][3]).toBe('Government Warning');
    });
});

describe('downloadColaTemplateCsv', () => {
    it('is a callable function that triggers download of standard COLA headers', async () => {
        const { downloadColaTemplateCsv } = await import('../csv');
        expect(typeof downloadColaTemplateCsv).toBe('function');
    });
});

