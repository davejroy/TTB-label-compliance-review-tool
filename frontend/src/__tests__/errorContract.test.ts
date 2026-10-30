import { describe, it, expect } from 'vitest';
import {
  areaToFileIndex,
  defaultPanelRole,
  nextUnusedPanelRole,
  httpStatusUserMessage,
  isErrorResult,
  isFailResult,
  isRetakeResult,
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
