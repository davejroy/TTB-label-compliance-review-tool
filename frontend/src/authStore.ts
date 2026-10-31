/**
 * Demo access token store for the TTB Label Compliance Review Tool frontend.
 *
 * Holds the demo access token used to authorize API calls when the backend's
 * DEMO_ACCESS_TOKEN gate is enabled. The token is kept in sessionStorage so it
 * survives page reloads within the same tab/session but is cleared when the tab
 * is closed. This is a *demonstration-grade* gate, not real user auth.
 */

const STORAGE_KEY = "ttb_demo_token";

/** Read the stored demo token, or null if the user has not logged in. */
export function getToken(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    // sessionStorage can throw in private-mode/embedded contexts.
    return null;
  }
}

/** Persist the demo token for this session. */
export function setToken(token: string): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, token);
  } catch {
    /* ignore storage failures; auth simply won't persist across reloads */
  }
}

/** Clear the stored demo token (log out). */
export function clearToken(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

/** Log out helper alias. */
export const logout = clearToken;

type UnauthorizedListener = () => void;
const unauthorizedListeners = new Set<UnauthorizedListener>();

/**
 * Subscribe to session invalidation (HTTP 401 on an authenticated API call).
 * Returns an unsubscribe function.
 */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener);
  return () => {
    unauthorizedListeners.delete(listener);
  };
}

/**
 * Clear the stored demo token and notify listeners so the login card can
 * be shown again. Safe to call when no token is stored.
 */
export function handleUnauthorized(): void {
  clearToken();
  for (const listener of unauthorizedListeners) {
    try {
      listener();
    } catch {
      // A listener failure must not break the 401 error path.
    }
  }
}

/**
 * Return an Authorization header object for fetch(), or an empty object when
 * no token is stored. Spread this into a request's headers:
 *
 *   fetch(url, { headers: { ...authHeader() } })
 *
 * When the backend gate is disabled the backend ignores the header, so it is
 * always safe to include.
 */
export function authHeader(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
