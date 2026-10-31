/** Shared Content-Security-Policy for the static frontend. */

export const KNOWN_BACKEND_ORIGINS = [
  "https://ttb-label-backend.onrender.com",
  "https://ttb-label-backend-dev-r1pp.onrender.com",
] as const;

export function buildConnectSrc(apiHost?: string): string {
  const origins = new Set<string>(["'self'", ...KNOWN_BACKEND_ORIGINS]);
  const trimmed = apiHost?.trim();
  if (trimmed) {
    origins.add(trimmed.startsWith("http") ? trimmed : `https://${trimmed}`);
  }
  return Array.from(origins).join(" ");
}

export function buildContentSecurityPolicy(
  apiHost?: string,
  options?: { frameAncestors?: boolean },
): string {
  const directives = [
    "default-src 'self'",
    `connect-src ${buildConnectSrc(apiHost)}`,
    "img-src 'self' data: blob:",
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self'",
    "object-src 'none'",
  ];
  // Browsers ignore frame-ancestors on a <meta> CSP and log a warning.
  // Keep it on HTTP headers (render.yaml / nginx) only.
  if (options?.frameAncestors !== false) {
    directives.push("frame-ancestors 'none'");
  }
  directives.push("base-uri 'self'");
  return directives.join("; ");
}
