import { describe, expect, it } from 'vitest';
import { resolveApiOrigin } from '../apiOrigin';
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
});
