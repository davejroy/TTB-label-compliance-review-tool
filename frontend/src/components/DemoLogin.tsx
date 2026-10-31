import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { resolveApiOrigin } from "../apiOrigin";
import { getToken, onUnauthorized, setToken } from "../authStore";
import { httpStatusUserMessage } from "../errorContract";

/** Shown when a stored token is rejected (HTTP 401) or the login probe fails. */
export const ACCESS_EXPIRED_MESSAGE = "Access code expired or incorrect";

/**
 * Demo access gate for the TTB Label Compliance Review Tool.
 *
 * Wraps the application. Behavior:
 *  - On mount it asks the backend (GET /api/demo-info) whether the access gate
 *    is enabled. The payload is only `{ auth_enabled }` — no username.
 *  - If the gate is DISABLED (auth_enabled === false), children render
 *    immediately — no login required. This is the default local-dev experience
 *    and guarantees the app is never accidentally locked.
 *  - If the gate is ENABLED and no valid token is stored, a login card is shown.
 *    Evaluators paste the access token provided to them.
 *  - If a later API call returns 401, `api.ts` clears the stored token and this
 *    card is shown again with "Access code expired or incorrect".
 *
 * This is a demonstration-grade shared-token gate, not real user authentication.
 */

const API_BASE = resolveApiOrigin(import.meta.env.VITE_API_HOST);

interface DemoInfo {
  auth_enabled: boolean;
}

type GateState =
  | { status: "loading" }
  | { status: "open" }
  | { status: "login" };

export default function DemoLogin({ children }: { children: ReactNode }) {
  const [gate, setGate] = useState<GateState>({ status: "loading" });
  const [tokenInput, setTokenInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submittingRef = useRef(false);
  const authEnabledRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/demo-info`);
        if (!res.ok) throw new Error(`demo-info ${res.status}`);
        const info: DemoInfo = await res.json();
        if (cancelled) return;
        authEnabledRef.current = Boolean(info.auth_enabled);
        if (!info.auth_enabled) {
          setGate({ status: "open" });
          return;
        }
        if (getToken()) {
          setGate({ status: "open" });
        } else {
          setGate({ status: "login" });
        }
      } catch {
        if (!cancelled) setGate({ status: "open" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return onUnauthorized(() => {
      if (!authEnabledRef.current) return;
      setError(ACCESS_EXPIRED_MESSAGE);
      setTokenInput("");
      setGate({ status: "login" });
    });
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (submittingRef.current || submitting) return;
    setError(null);
    const token = tokenInput.trim();
    if (!token) {
      setError("Enter the access token provided for the demo.");
      return;
    }
    submittingRef.current = true;
    setSubmitting(true);
    try {
      const probe = await fetch(`${API_BASE}/api/review/batch`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: new FormData(),
      });
      if (probe.status === 401) {
        setError(ACCESS_EXPIRED_MESSAGE);
        return;
      }
      if (probe.status === 429) {
        setError(httpStatusUserMessage(429, "Too many failed sign-in attempts. Please wait a few minutes and try again."));
        return;
      }
      if (probe.status === 413) {
        setError(httpStatusUserMessage(413, "This upload is too large."));
        return;
      }
      setToken(token);
      setGate({ status: "open" });
    } catch {
      setError("Could not reach the server to verify the token. Try again.");
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  if (gate.status === "loading") {
    return (
      <div className="min-h-screen grid place-items-center bg-slate-100 text-slate-500">
        Loading…
      </div>
    );
  }

  if (gate.status === "open") {
    return <>{children}</>;
  }

  return (
    <div className="min-h-screen grid place-items-center bg-slate-100 px-4">
      <div className="w-full max-w-md rounded-xl border border-slate-300 bg-white shadow-sm">
        <div className="bg-[#083c6f] rounded-t-xl border-b-4 border-[#ffbe2e] px-6 py-4">
          <h1 className="text-xl font-bold text-white">Label Compliance Review Tool</h1>
          <p className="text-sm text-slate-200">Demo access</p>
        </div>
        <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4">
          <p className="text-sm text-slate-600">
            This demonstration environment is access-controlled. Use the
            credentials provided for your evaluation session.
          </p>
          <label className="block">
            <span className="text-sm font-medium text-slate-700">Access token</span>
            <input
              type="password"
              autoComplete="off"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-[#15396a] focus:outline-none focus:ring-1 focus:ring-[#15396a]"
              placeholder="Paste the demo access token"
            />
          </label>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-md bg-[#15396a] px-4 py-2.5 text-sm font-semibold text-white hover:bg-[#0f2d54] disabled:opacity-60"
          >
            {submitting ? "Verifying…" : "Enter"}
          </button>
        </form>
      </div>
    </div>
  );
}
