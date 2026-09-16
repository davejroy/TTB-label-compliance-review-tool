import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { getToken, setToken } from "../authStore";

/**
 * Demo access gate for the TTB Label Compliance Review Tool.
 *
 * Wraps the application. Behavior:
 *  - On mount it asks the backend (GET /api/demo-info) whether the access gate
 *    is enabled and, if so, which username evaluators should use.
 *  - If the gate is DISABLED (auth_enabled === false), children render
 *    immediately — no login required. This is the default local-dev experience
 *    and guarantees the app is never accidentally locked.
 *  - If the gate is ENABLED and no valid token is stored, a login card is shown
 *    that DISPLAYS the expected demo username so TTB evaluators are never locked
 *    out. They paste the access token provided to them; it is validated against
 *    the backend and stored for the session.
 *
 * This is a demonstration-grade shared-token gate, not real user authentication.
 */

// Mirror the API base resolution used in api.ts so we hit the same backend.
const API_BASE = import.meta.env.VITE_API_HOST
  ? `https://${import.meta.env.VITE_API_HOST}`
  : "";

interface DemoInfo {
  auth_enabled: boolean;
  username: string;
}

type GateState =
  | { status: "loading" }
  | { status: "open" } // auth disabled or already authenticated
  | { status: "login"; username: string };

export default function DemoLogin({ children }: { children: ReactNode }) {
  const [gate, setGate] = useState<GateState>({ status: "loading" });
  const [tokenInput, setTokenInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submittingRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/demo-info`);
        if (!res.ok) throw new Error(`demo-info ${res.status}`);
        const info: DemoInfo = await res.json();
        if (cancelled) return;
        if (!info.auth_enabled) {
          setGate({ status: "open" });
          return;
        }
        // Gate is on: if we already hold a token, trust it for now (a bad token
        // will be rejected by the first real API call, which clears it).
        if (getToken()) {
          setGate({ status: "open" });
        } else {
          setGate({ status: "login", username: info.username });
        }
      } catch {
        // If demo-info is unreachable, fail OPEN rather than lock the evaluator
        // out. The backend is the real enforcement point; the UI gate is a
        // convenience. A protected call will still 401 if a token is required.
        if (!cancelled) setGate({ status: "open" });
      }
    })();
    return () => {
      cancelled = true;
    };
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
      // Validate the token against a protected endpoint via a cheap OPTIONS-like
      // probe. We use /api/demo-info to confirm reachability, then store and let
      // the app's real calls exercise the token. To actively verify, we hit a
      // protected route with a HEAD-style empty request expecting 401 vs !=401.
      const probe = await fetch(`${API_BASE}/api/review/batch`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: new FormData(),
      });
      if (probe.status === 401) {
        setError("That access token was not accepted. Please check and retry.");
        return;
      }
      // Any non-401 (including 422 for the empty body) means the token passed
      // the auth layer. Store it and enter the app.
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

  // gate.status === "login"
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
          <div className="rounded-md bg-slate-50 border border-slate-200 px-3 py-2 text-sm">
            <div className="text-slate-500">Demo username</div>
            <div className="font-mono font-semibold text-slate-800">{gate.username}</div>
          </div>
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
