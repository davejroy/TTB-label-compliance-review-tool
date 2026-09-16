import { useState } from "react";
import SingleReview from "./components/SingleReview";
import BatchReview from "./components/BatchReview";
import LabelOnlyCheck from "./components/LabelOnlyCheck";
import DemoLogin from "./components/DemoLogin";
import InstructionsModal from "./components/InstructionsModal";

type Tab = "single" | "batch" | "label-check";

/**
 * Top-level layout: header, the three review-mode tabs (Single Label,
 * Batch Review, Label-Only Check), and a footer disclaimer. Each tab
 * renders an independent, self-contained component that manages its own
 * upload/results state.
 *
 * Tab panels are rendered but hidden with CSS (display:none) rather than
 * unmounted when inactive. This preserves in-progress form data and results
 * when the user switches tabs and returns — no data loss on accidental tab clicks.
 *
 * The header reuses TTB's official logo (public/ttb-logo.png) and
 * ttb.gov's navy/gold color scheme (#083c6f background, #ffbe2e
 * accent border) so the tool reads as part of the TTB site family.
 *
 * The cold-start health-check ping that previously fired on every page
 * load has been removed: the backend now runs on a paid Standard instance
 * that never sleeps, so the warm-up request is no longer needed.
 */
export default function App() {
  const [tab, setTab] = useState<Tab>("single");
  const [showInstructions, setShowInstructions] = useState(false);

  const tabs: { id: Tab; label: string }[] = [
    { id: "single", label: "Single Label" },
    { id: "batch", label: "Batch Review" },
    { id: "label-check", label: "Label-Only Check" },
  ];

  return (
    <DemoLogin>
    <div className="min-h-screen bg-slate-100">
      <div className="bg-[#15396a] h-1.5" />
      <header className="bg-[#083c6f] border-b-4 border-[#ffbe2e]">
        <div className="max-w-6xl mx-auto px-4 py-6 flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-4">
            <img
              src="/ttb-logo.png"
              alt="TTB - Alcohol and Tobacco Tax and Trade Bureau, U.S. Department of the Treasury"
              className="h-20 w-auto flex-shrink-0"
            />
            <div>
              <h1 className="text-3xl font-bold text-white">Label Compliance Review Tool</h1>
              <p className="text-lg text-slate-200 mt-1">
                Upload a label image and compare it against the application details.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setShowInstructions(true)}
            className="inline-flex items-center gap-2 rounded-lg bg-[#ffbe2e] hover:bg-[#e5aa29] text-[#083c6f] px-4 py-2 text-sm font-bold shadow-sm transition-colors focus:outline-none focus:ring-2 focus:ring-white"
            aria-label="Open instructions and help guide"
          >
            <span aria-hidden="true">📖</span> Instructions
          </button>
        </div>
      </header>

      <nav className="max-w-6xl mx-auto px-4 mt-6" aria-label="Review mode">
        <div className="inline-flex rounded-lg border border-slate-300 bg-white p-1 shadow-sm">
          {tabs.map(({ id, label }) => (
            <button
              key={id}
              className={`rounded-md px-5 py-2.5 text-base font-semibold transition-colors ${
                tab === id ? "bg-[#15396a] text-white" : "text-slate-700 hover:bg-slate-100"
              }`}
              onClick={() => setTab(id)}
              aria-selected={tab === id}
            >
              {label}
            </button>
          ))}
        </div>
      </nav>

      <main className="max-w-6xl mx-auto px-4 py-8">
        {/*
          Each panel stays mounted so in-progress uploads/results are preserved
          when the user switches tabs and returns. The hidden panel is visually
          removed with display:none (Tailwind: hidden) but keeps its React state.
        */}
        <div className={tab === "single" ? "" : "hidden"}>
          <SingleReview />
        </div>
        <div className={tab === "batch" ? "" : "hidden"}>
          <BatchReview />
        </div>
        <div className={tab === "label-check" ? "" : "hidden"}>
          <LabelOnlyCheck />
        </div>
      </main>

      <footer className="max-w-6xl mx-auto px-4 py-8 text-sm text-slate-400">
        Prototype for evaluation purposes only. Not connected to the COLA system.
      </footer>

      <InstructionsModal
        isOpen={showInstructions}
        onClose={() => setShowInstructions(false)}
      />
    </div>
    </DemoLogin>
  );
}
