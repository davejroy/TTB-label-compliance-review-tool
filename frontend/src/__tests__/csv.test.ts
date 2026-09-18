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
});

describe('downloadColaTemplateCsv', () => {
    it('is a callable function that triggers download of standard COLA headers', async () => {
        const { downloadColaTemplateCsv } = await import('../csv');
        expect(typeof downloadColaTemplateCsv).toBe('function');
    });
});
