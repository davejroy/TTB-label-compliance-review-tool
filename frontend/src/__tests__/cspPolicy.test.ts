import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { buildConnectSrc, buildContentSecurityPolicy } from '../cspPolicy';

const REQUIRED_DIRECTIVES = [
  "default-src 'self'",
  "img-src 'self' data: blob:",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
];

describe('frontend CSP', () => {
  it('includes self, backend origins, blob images, and deny-by-default object/frame', () => {
    const policy = buildContentSecurityPolicy('ttb-label-backend.onrender.com');
    for (const directive of REQUIRED_DIRECTIVES) {
      expect(policy).toContain(directive);
    }
    expect(policy).toContain("connect-src");
    expect(policy).toContain("'self'");
    expect(policy).toContain('https://ttb-label-backend-dev-r1pp.onrender.com');
    expect(policy).toContain('https://ttb-label-backend.onrender.com');
    expect(policy).toContain("style-src 'self' 'unsafe-inline'");
  });

  it('keeps connect-src fail-open to known backends when VITE_API_HOST is unset', () => {
    const connect = buildConnectSrc();
    expect(connect).toContain("'self'");
    expect(connect).toContain('https://ttb-label-backend.onrender.com');
    expect(connect).toContain('https://ttb-label-backend-dev-r1pp.onrender.com');
  });

  it('omits frame-ancestors from the meta-tag policy (browsers ignore it there)', () => {
    const metaPolicy = buildContentSecurityPolicy(
      'ttb-label-backend.onrender.com',
      { frameAncestors: false },
    );
    expect(metaPolicy).not.toContain('frame-ancestors');
    expect(metaPolicy).toContain("object-src 'none'");
    expect(metaPolicy).toContain("base-uri 'self'");
  });

  it('loads VITE_API_HOST from mode env files via Vite loadEnv', () => {
    const viteConfig = readFileSync(resolve(__dirname, '../../vite.config.ts'), 'utf8');
    expect(viteConfig).toContain('loadEnv');
    expect(viteConfig).toContain('VITE_API_HOST');
  });

  it('declares the same policy on the Render static site', () => {
    const yaml = readFileSync(resolve(__dirname, '../../../render.yaml'), 'utf8');
    expect(yaml).toContain('Content-Security-Policy');
    expect(yaml).toContain("default-src 'self'");
    expect(yaml).toContain("img-src 'self' data: blob:");
    expect(yaml).toContain("object-src 'none'");
    expect(yaml).toContain("frame-ancestors 'none'");
    expect(yaml).toContain("base-uri 'self'");
    expect(yaml).toContain('https://ttb-label-backend.onrender.com');
  });
});
