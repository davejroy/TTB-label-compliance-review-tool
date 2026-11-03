/** Resolve the backend origin used by fetch and CSP connect-src.

Vite bakes `VITE_API_HOST` at build time. Production values are a hostname
(`ttb-label-backend.onrender.com`). Local / Docker leave it empty so
requests stay same-origin (`/api`). A value that already includes a scheme is
kept as-is so CSP and `API_BASE` never disagree.
*/
export function resolveApiOrigin(apiHost?: string): string {
  const trimmed = apiHost?.trim();
  if (!trimmed) {
    return "";
  }
  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed.replace(/\/$/, "");
  }
  return `https://${trimmed}`;
}
