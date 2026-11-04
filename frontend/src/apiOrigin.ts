/** Resolve the backend origin used by fetch and CSP connect-src.

Vite bakes `VITE_API_HOST` at build time. Production values are a hostname
(`ttb-label-backend.onrender.com`). Local / Docker leave it empty so
requests stay same-origin (`/api`). A value that already includes a scheme is
kept as-is so CSP and `API_BASE` never disagree.

Rejected values fall back to same-origin (`""`) so quotes, spaces, schemes
other than http(s), or a path cannot reach `fetch` or the CSP `connect-src`
list. The Vite build also fails when a non-empty host does not match.
*/

/** Optional http(s) scheme, `[A-Za-z0-9.-]` hostname, optional port, optional `/`. */
export const API_ORIGIN_PATTERN =
  /^(https?:\/\/)?[A-Za-z0-9.-]+(:\d{1,5})?\/?$/;

export function isAllowedApiOrigin(apiHost: string): boolean {
  const trimmed = apiHost.trim();
  if (!trimmed) {
    return false;
  }
  const normalized = trimmed.replace(/^https?:\/\//i, (match) =>
    match.toLowerCase(),
  );
  return API_ORIGIN_PATTERN.test(normalized);
}

export function resolveApiOrigin(apiHost?: string): string {
  const trimmed = apiHost?.trim();
  if (!trimmed || !isAllowedApiOrigin(trimmed)) {
    return "";
  }
  const withoutSlash = trimmed.replace(/\/$/, "");
  if (/^https?:\/\//i.test(withoutSlash)) {
    return withoutSlash;
  }
  return `https://${withoutSlash}`;
}
