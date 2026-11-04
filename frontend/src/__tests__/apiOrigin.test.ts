import { describe, expect, it } from 'vitest';
import { isAllowedApiOrigin, resolveApiOrigin } from '../apiOrigin';
import { buildConnectSrc } from '../cspPolicy';

describe('resolveApiOrigin vs connect-src', () => {
  it('matches hostname-only VITE_API_HOST to https connect-src', () => {
    const host = 'api.example.test';
    const origin = resolveApiOrigin(host);
    expect(origin).toBe(`https://${host}`);
    expect(buildConnectSrc(host).split(' ')).toContain(origin);
  });

  it('does not double-prefix a scheme', () => {
    const origin = resolveApiOrigin('https://example.test');
    expect(origin).toBe('https://example.test');
    expect(buildConnectSrc('https://example.test')).toContain('https://example.test');
    expect(buildConnectSrc('https://example.test')).not.toContain('https://https://');
  });

  it('uses same-origin when the host is empty', () => {
    expect(resolveApiOrigin('')).toBe('');
    expect(resolveApiOrigin(undefined)).toBe('');
    expect(buildConnectSrc()).toContain("'self'");
  });

  it('accepts an optional port and trailing slash', () => {
    expect(resolveApiOrigin('https://api.example.test:8443/')).toBe(
      'https://api.example.test:8443',
    );
    expect(isAllowedApiOrigin('localhost:8000')).toBe(true);
    expect(resolveApiOrigin('localhost:8000')).toBe('https://localhost:8000');
  });

  it.each([
    ['quotes', 'evil.com"onclick="alert(1)'],
    ['angle brackets', 'evil.com><script>'],
    ['spaces', 'api.example.test /admin'],
    ['javascript scheme', 'javascript:alert(1)'],
    ['path injection', 'api.example.test/api/../admin'],
  ])('rejects %s and falls back to same-origin', (_label, value) => {
    expect(isAllowedApiOrigin(value)).toBe(false);
    expect(resolveApiOrigin(value)).toBe('');
    expect(buildConnectSrc(value)).not.toContain(value);
  });
});
