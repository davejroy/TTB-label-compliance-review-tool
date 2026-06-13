import { useEffect, useState } from "react";
import SingleReview from "./components/SingleReview";
import BatchReview from "./components/BatchReview";
import LabelOnlyCheck from "./components/LabelOnlyCheck";

type Tab = "single" | "batch" | "label-check";

// Resolve the same API base that api.ts uses.
const API_BASE = import.meta.env.VITE_API_HOST
  ? `https://${import.meta.env.VITE_API_HOST}`
    : "";

/**
 * Top-level layout: header, the three review-mode tabs (Single Label,
 * Batch Review, Label-Only Check), and a footer disclaimer. Each tab
 * renders an independent, self-contained component that manages its own
 * upload/results state.
 *
 * The header reuses TTB's official logo (`public/ttb-logo.png`) and
 * ttb.gov's navy/gold color scheme (`#083c6f` background, `#ffbe2e`
 * accent border) so the tool reads as part of the TTB site family.
 *
 * On mount a fire-and-forget GET /api/health request is sent to warm up
 * the Render free-tier backend, which spins down after inactivity and
 * can take 30–60 s to wake. Sending the request while the user reads
 * the UI typically absorbs most or all of that latency.
 */
export default function App() {
    const [tab, setTab] = useState<Tab>("single");

  // Warm up the backend on page load to reduce cold-start latency on
  // Render's free tier (services sleep after inactivity).
  useEffect(() => {
        fetch(`${API_BASE}/api/health`).catch(() => {
                // Ignore – this is best-effort. The real request will show any error.
        });
  }, []);

  return (
        <div className="min-h-screen bg-slate-100">
              <div className="bg-[#15396a] h-1.5" />
              <header className="bg-[#083c6f] border-b-4 border-[#ffbe2e]">
                      <div className="max-w-6xl mx-auto px-4 py-6 flex flex-wrap items-center gap-4">
                                <img src="/ttb-logo.png" alt="TTB - Alcohol and Tobacco Tax and Trade Bureau, U.S. Department of the Treasury" className="h-16 w-auto flex-shrink-0" />
                                <div>
                                            <h1 className="text-3xl font-bold text-white">Label Compliance Review Tool</h1>h1>
                                            <p className="text-lg text-slate-200 mt-1">
                                                          Upload a label image and compare it against the application details.
                                            </p>p>
                                </div>div>
                      </div>div>
              </header>header>
        
              <nav className="max-w-6xl mx-auto px-4 mt-6">
                      <div className="inline-flex rounded-lg border border-slate-300 bg-white p-1 shadow-sm">
                                <button
                                              className={`rounded-md px-5 py-2.5 text-base font-semibold transition-colors ${
                                                              tab === "single" ? "bg-[#15396a] text-white" : "text-slate-700 hover:bg-slate-100"
                                              }`}
                                              onClick={() => setTab("single")}
                                            >
                                            Single Label
                                </button>button>
                                <button
                                              className={`rounded-md px-5 py-2.5 text-base font-semibold transition-colors ${
                                                              tab === "batch" ? "bg-[#15396a] text-white" : "text-slate-700 hover:bg-slate-100"
                                              }`}
                                              onClick={() => setTab("batch")}
                                            >
                                            Batch Review
                                </button>button>
                                <button
                                              className={`rounded-md px-5 py-2.5 text-base font-semibold transition-colors ${
                                                              tab === "label-check" ? "bg-[#15396a] text-white" : "text-slate-700 hover:bg-slate-100"
                                              }`}
                                              onClick={() => setTab("label-check")}
                                            >
                                            Label-Only Check
                                </button>button>
                      </div>div>
              </nav>nav>
        
              <main className="max-w-6xl mx-auto px-4 py-8">
                {tab === "single" && <SingleReview />}
                {tab === "batch" && <BatchReview />}
                {tab === "label-check" && <LabelOnlyCheck />}
              </main>main>
        
              <footer className="max-w-6xl mx-auto px-4 py-8 text-sm text-slate-400">
                      Prototype for evaluation purposes only. Not connected to the COLA system.
              </footer>footer>
        </div>div>
      );
}</div>
