import { useState } from "react";
import SingleReview from "./components/SingleReview";
import BatchReview from "./components/BatchReview";
import LabelOnlyCheck from "./components/LabelOnlyCheck";

type Tab = "single" | "batch" | "label-check";

/**
 * Top-level layout: header, the three review-mode tabs (Single Label,
 * Batch Review, Label-Only Check), and a footer disclaimer. Each tab
 * renders an independent, self-contained component that manages its own
 * upload/results state.
 */
export default function App() {
  const [tab, setTab] = useState<Tab>("single");

  return (
    <div className="min-h-screen bg-slate-100">
      <div className="bg-[#15396a] h-1.5" />
      <header className="bg-[#0b1f3a] border-b-4 border-[#ffbe2e]">
        <div className="max-w-6xl mx-auto px-4 py-6 flex items-center gap-4">
          <img src="/ttb-logo.svg" alt="TTB seal" className="h-14 w-14 flex-shrink-0" />
          <div>
            <p className="text-xs font-semibold uppercase tracking-widest text-[#ffbe2e]">
              Alcohol and Tobacco Tax and Trade Bureau &middot; U.S. Department of the Treasury
            </p>
            <h1 className="text-3xl font-bold text-white">TTB Label Compliance Review Tool</h1>
            <p className="text-lg text-slate-200 mt-1">
              Upload a label image and compare it against the application details.
            </p>
          </div>
        </div>
      </header>

      <nav className="max-w-6xl mx-auto px-4 mt-6">
        <div className="inline-flex rounded-lg border border-slate-300 bg-white p-1 shadow-sm">
          <button
            className={`rounded-md px-5 py-2.5 text-base font-semibold transition-colors ${
              tab === "single" ? "bg-[#15396a] text-white" : "text-slate-700 hover:bg-slate-100"
            }`}
            onClick={() => setTab("single")}
          >
            Single Label
          </button>
          <button
            className={`rounded-md px-5 py-2.5 text-base font-semibold transition-colors ${
              tab === "batch" ? "bg-[#15396a] text-white" : "text-slate-700 hover:bg-slate-100"
            }`}
            onClick={() => setTab("batch")}
          >
            Batch Review
          </button>
          <button
            className={`rounded-md px-5 py-2.5 text-base font-semibold transition-colors ${
              tab === "label-check" ? "bg-[#15396a] text-white" : "text-slate-700 hover:bg-slate-100"
            }`}
            onClick={() => setTab("label-check")}
          >
            Label-Only Check
          </button>
        </div>
      </nav>

      <main className="max-w-6xl mx-auto px-4 py-8">
        {tab === "single" && <SingleReview />}
        {tab === "batch" && <BatchReview />}
        {tab === "label-check" && <LabelOnlyCheck />}
      </main>

      <footer className="max-w-6xl mx-auto px-4 py-8 text-sm text-slate-400">
        Prototype for evaluation purposes only. Not connected to the COLA system.
      </footer>
    </div>
  );
}
