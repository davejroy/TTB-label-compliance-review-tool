import { describe, it, expect } from 'vitest';
import {
  areaToFileIndex,
  defaultPanelRole,
  nextUnusedPanelRole,
  httpStatusUserMessage,
  isErrorResult,
  canExportResults,
  isExportableResult,
  isFailResult,
  isRetakeResult,
  isServiceUnavailableCode,
} from '../errorContract';

describe('result classification', () => {
  it('classifies retake from overall_status, not the message string', () => {
    expect(isRetakeResult({ overall_status: 'retake', error_code: null })).toBe(true);
    expect(isRetakeResult({
      overall_status: 'fail',
      error_code: 'PHOTO_UNREADABLE_FIELD',
    })).toBe(true);
    expect(isErrorResult({ overall_status: 'retake', error_code: 'PHOTO_QUALITY_LOW' })).toBe(false);
  });

  it('classifies system errors separately from compliance fail', () => {
    expect(isErrorResult({ overall_status: 'error', error_code: 'UPSTREAM_TIMEOUT' })).toBe(true);
    expect(isFailResult({ overall_status: 'fail' })).toBe(true);
    expect(isFailResult({ overall_status: 'error' })).toBe(false);
    expect(isErrorResult({ overall_status: 'fail', error_code: null })).toBe(false);
  });
});

describe('httpStatusUserMessage', () => {
  it('maps 401, 413, and 429 to clear user-facing copy', () => {
    expect(httpStatusUserMessage(401, 'fallback')).toMatch(/not authorized/i);
    expect(httpStatusUserMessage(413, 'fallback')).toMatch(/too large/i);
    expect(httpStatusUserMessage(429, 'fallback')).toMatch(/too many requests/i);
    expect(httpStatusUserMessage(500, 'Server Error')).toBe('Server Error');
  });

  it('unwraps 503 VISION_UNAVAILABLE into the photo-is-fine message', () => {
    expect(
      httpStatusUserMessage(
        503,
        'Review failed (503): The label reader is unavailable right now — your photo is fine. Try again shortly.',
        'VISION_UNAVAILABLE',
      ),
    ).toBe('The label reader is unavailable right now — your photo is fine. Try again shortly.');
    expect(isServiceUnavailableCode('VISION_UNAVAILABLE')).toBe(true);
    expect(isServiceUnavailableCode('CONFIG_ERROR')).toBe(true);
    expect(isServiceUnavailableCode('UPSTREAM_TIMEOUT')).toBe(true);
    expect(isServiceUnavailableCode('RATE_LIMITED')).toBe(true);
    expect(isExportableResult({ overall_status: 'error', error_code: 'VISION_UNAVAILABLE' })).toBe(false);
    expect(isExportableResult({ overall_status: 'fail', error_code: null })).toBe(true);
    expect(isExportableResult({ overall_status: 'retake', error_code: 'PHOTO_QUALITY_LOW' })).toBe(true);
    expect(canExportResults([{ overall_status: 'error', error_code: 'VISION_UNAVAILABLE' }])).toBe(true);
    expect(canExportResults([])).toBe(false);
  });

  it('unwraps 502 / 504 / 429 vision codes without a green-path message', () => {
    expect(
      httpStatusUserMessage(502, 'Review failed (502): The label reader is unavailable right now — your photo is fine. Try again shortly.', 'UPSTREAM_ERROR'),
    ).toMatch(/photo is fine/i);
    expect(
      httpStatusUserMessage(504, 'Review failed (504): The label reader timed out — your photo is fine. Try again shortly.', 'UPSTREAM_TIMEOUT'),
    ).toMatch(/timed out/i);
    expect(
      httpStatusUserMessage(429, 'The label reader is busy — your photo is fine. Wait a moment and try again.', 'RATE_LIMITED'),
    ).toMatch(/photo is fine/i);
  });

  it('shows the structured 413 FILE_TOO_LARGE message from the API', () => {
    expect(
      httpStatusUserMessage(
        413,
        'The upload is too large. Please use fewer or smaller photos.',
        'FILE_TOO_LARGE',
      ),
    ).toBe('The upload is too large. Please use fewer or smaller photos.');
  });

  it.each([
    'Review failed (413): The upload is too large. Please use fewer or smaller photos.',
    'Batch review failed (413): The upload is too large. Please use fewer or smaller photos.',
    'Batch stream failed (413): The upload is too large. Please use fewer or smaller photos.',
    'Template import failed (413): The template file is too large. Please upload a file under 1 MB.',
    'Failed to load presets (413): The upload is too large. Please use fewer or smaller photos.',
  ])('unwraps API 413 wrapper %s so users see the FILE_TOO_LARGE text', (wrapped) => {
    const shown = httpStatusUserMessage(413, wrapped, 'FILE_TOO_LARGE');
    expect(shown).not.toMatch(/failed \(\d+\)/i);
    expect(shown).toMatch(/too large/i);
    if (wrapped.includes('template file')) {
      expect(shown).toBe('The template file is too large. Please upload a file under 1 MB.');
    } else {
      expect(shown).toBe('The upload is too large. Please use fewer or smaller photos.');
    }
  });

  it('does not surface a proxy HTML 413 page', () => {
    const html = '<html><head><title>413 Request Entity Too Large</title></head><body>nginx</body></html>';
    const shown = httpStatusUserMessage(413, html);
    expect(shown).toMatch(/too large/i);
    expect(shown).not.toMatch(/<html/i);
    expect(shown).not.toMatch(/request entity too large/i);
    expect(shown).not.toMatch(/nginx/i);
  });
});

describe('areaToFileIndex', () => {
  it('uses explicit panel roles when the upload order is not front-then-back', () => {
    expect(areaToFileIndex('back', 2, ['back', 'front'])).toBe(0);
    expect(areaToFileIndex('front', 2, ['back', 'front'])).toBe(1);
  });

  it('falls back to slot convention when roles are missing', () => {
    expect(areaToFileIndex('front', 2)).toBe(0);
    expect(areaToFileIndex('back', 2)).toBe(1);
    expect(areaToFileIndex('neck', 3)).toBe(2);
    expect(areaToFileIndex('back', 1)).toBe(0);
  });

  it('assigns default panel roles by upload order', () => {
    expect(defaultPanelRole(0)).toBe('front');
    expect(defaultPanelRole(1)).toBe('back');
    expect(defaultPanelRole(2)).toBe('neck');
  });

  it('picks the first unused panel role after a removal', () => {
    expect(nextUnusedPanelRole(['back'])).toBe('front');
    expect(nextUnusedPanelRole(['front', 'neck'])).toBe('back');
    expect(nextUnusedPanelRole(['front', 'back'])).toBe('neck');
    expect(nextUnusedPanelRole(['front', 'back', 'neck'])).toBe('front');
  });
});
