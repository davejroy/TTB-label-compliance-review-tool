// @vitest-environment node
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { resolveApiOrigin } from '../apiOrigin';
import { buildConnectSrc, buildContentSecurityPolicy } from '../cspPolicy';
import { escapeHtmlAttr } from '../htmlAttr';

const VITE_CONFIG_PATH = fileURLToPath(
  new URL('../../vite.config.ts', import.meta.url),
);
const RENDER_YAML_PATH = fileURLToPath(
  new URL('../../../render.yaml', import.meta.url),
);

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
    expect(policy).toContain('https://ttb-label-backend.onrender.com');
    expect(policy).toContain("style-src 'self' 'unsafe-inline'");
  });

  it('keeps connect-src fail-open to known backends when VITE_API_HOST is unset', () => {
    const connect = buildConnectSrc();
    expect(connect).toContain("'self'");
    expect(connect).toContain('https://ttb-label-backend.onrender.com');
  });

  it('keeps connect-src fail-open to known backends when VITE_API_HOST is invalid', () => {
    const connect = buildConnectSrc('javascript:alert(1)');
    expect(connect).toContain("'self'");
    expect(connect).toContain('https://ttb-label-backend.onrender.com');
    expect(connect).not.toContain('javascript:');
  });

  it('allows the same origin resolveApiOrigin would fetch', () => {
    const host = 'api.example.test';
    const origin = resolveApiOrigin(host);
    expect(origin).toBe('https://api.example.test');
    expect(buildConnectSrc(host)).toContain(origin);
    expect(buildConnectSrc('https://custom-backend.example')).toContain(
      'https://custom-backend.example',
    );
    expect(resolveApiOrigin('https://custom-backend.example')).toBe(
      'https://custom-backend.example',
    );
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

  it('loads only VITE_ vars from mode env files via Vite loadEnv', () => {
    const viteConfig = readFileSync(VITE_CONFIG_PATH, 'utf8');
    expect(viteConfig).toContain('loadEnv');
    expect(viteConfig).toContain("loadEnv(mode, process.cwd(), 'VITE_')");
    expect(viteConfig).toContain('VITE_API_HOST');
    expect(viteConfig).toContain('escapeHtmlAttr(csp)');
  });

  it('rejects hosts that would break out of the CSP meta attribute', () => {
    const injected = 'evil"><script>alert(1)</script>';
    expect(resolveApiOrigin(injected)).toBe('');
    expect(buildConnectSrc(injected)).not.toContain(injected);
    expect(buildContentSecurityPolicy(injected)).not.toContain('<script>');
    expect(escapeHtmlAttr('a&b"c<d>e\'f')).toBe(
      'a&amp;b&quot;c&lt;d&gt;e&#39;f',
    );
  });

  it('attribute-escapes a valid CSP to an equivalent HTML-decoded value', () => {
    const policy = buildContentSecurityPolicy('api.example.test', {
      frameAncestors: false,
    });
    const escaped = escapeHtmlAttr(policy);
    const decoded = escaped
      .replace(/&quot;/g, '"')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&#39;/g, "'")
      .replace(/&amp;/g, '&');
    expect(decoded).toBe(policy);
    // Valid policies have no & " < >, so those escapes are identity.
    expect(policy).not.toMatch(/[&"<>]/);
  });

  it('declares the same policy on the Render static site', () => {
    const yaml = readFileSync(RENDER_YAML_PATH, 'utf8');
    expect(yaml).toContain('Content-Security-Policy');
    expect(yaml).toContain("default-src 'self'");
    expect(yaml).toContain("img-src 'self' data: blob:");
    expect(yaml).toContain("object-src 'none'");
    expect(yaml).toContain("frame-ancestors 'none'");
    expect(yaml).toContain("base-uri 'self'");
    expect(yaml).toContain('https://ttb-label-backend.onrender.com');
  });
});
