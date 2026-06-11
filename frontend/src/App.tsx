import { useState } from "react";
import SingleReview from "./components/SingleReview";
import BatchReview from "./components/BatchReview";

type Tab = "single" | "batch";

export default function App() {
  const [tab, setTab] = useState<Tab>("single");

  return (
    <div className="min-h-screen bg-slate-100">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-4 py-6">
          <h1 className="text-3xl font-bold text-slate-900">TTB Label Compliance Review Tool</h1>
          <p className="text-lg text-slate-600 mt-1">
            Upload a label image and compare it against the application details.
          </p>
        </div>
      </header>

      <nav className="max-w-6xl mx-auto px-4 mt-6">
        <div className="inline-flex rounded-lg border border-slate-300 bg-white p-1 shadow-sm">
          <button
            className={`rounded-md px-5 py-2.5 text-base font-semibold transition-colors ${
              tab === "single" ? "bg-blue-600 text-white" : "text-slate-700 hover:bg-slate-100"
            }`}
            onClick={() => setTab("single")}
          >
            Single Label
          </button>
          <button
            className={`rounded-md px-5 py-2.5 text-base font-semibold transition-colors ${
              tab === "batch" ? "bg-blue-600 text-white" : "text-slate-700 hover:bg-slate-100"
            }`}
            onClick={() => setTab("batch")}
          >
            Batch Review
          </button>
        </div>
      </nav>

      <main className="max-w-6xl mx-auto px-4 py-8">
        {tab === "single" ? <SingleReview /> : <BatchReview />}
      </main>

      <footer className="max-w-6xl mx-auto px-4 py-8 text-sm text-slate-400">
        Prototype for evaluation purposes only. Not connected to the COLA system.
      </footer>
    </div>
  );
}
