import { useEffect, useRef, useState } from "react";
import { reviewLabel } from "../api";
import { EMPTY_APPLICATION, type ApplicationData, type ReviewResult } from "../types";
import ApplicationForm from "./ApplicationForm";
import ImageDropzone from "./ImageDropzone";
import ProcessingStatusBar from "./ProcessingStatusBar";
import ResultsPanel from "./ResultsPanel";

// Step indices for ProcessingStatusBar
// 0 = Uploading  1 = Reading label  2 = Checking  3 = Complete
const STEP_UPLOAD   = 0;
const STEP_READING  = 1;
const STEP_CHECKING = 2;
const STEP_COMPLETE = 3;

export default function SingleReview() {
  const [application, setApplication] = useState<ApplicationData>(EMPTY_APPLICATION);
  const [files, setFiles] = useState<File[]>([]);
  const [result, setResult] = useState<ReviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusStep, setStatusStep] = useState(-1);
  const [showBar, setShowBar] = useState(false);
  const stepTimers = useRef<ReturnType<typeof setTimeout>[]>([]);

  function clearTimers() {
    stepTimers.current.forEach(clearTimeout);
    stepTimers.current = [];
  }

  function startProgressTimers() {
    clearTimers();
    setStatusStep(STEP_UPLOAD);
    // Advance to "Reading label" after ~1s (upload is fast)
    stepTimers.current.push(setTimeout(() => setStatusStep(STEP_READING), 1000));
    // Advance to "Checking" after ~4s (Claude extraction typically 2-5s)
    stepTimers.current.push(setTimeout(() => setStatusStep(STEP_CHECKING), 4000));
    // Stay on Checking until the real result arrives
  }

  const canSubmit =
    files.length > 0 &&
    application.brand_name.trim() !== "" &&
    application.class_type.trim() !== "" &&
    application.alcohol_content.trim() !== "" &&
    application.net_contents.trim() !== "" &&
    !loading;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (files.length === 0) return;
    setLoading(true);
    setShowBar(true);
    setError(null);
    setResult(null);
    startProgressTimers();
    try {
      const res = await reviewLabel(files, application);
      clearTimers();
      setStatusStep(STEP_COMPLETE);
      setResult(res);
    } catch (err) {
      clearTimers();
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  // Decide what to show in the results panel
  const showStatusBar = showBar && (loading || statusStep === STEP_COMPLETE);
  const isDone = statusStep === STEP_COMPLETE;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
      <form onSubmit={handleSubmit} className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
        <h2 className="text-2xl font-bold text-slate-900">Step 1: Enter COLA Application Details</h2>
        <p className="text-base text-slate-500 mb-4">
          Type in the details exactly as they appear on the approved COLA application.
        </p>
        <ApplicationForm value={application} onChange={setApplication} idPrefix="single" />

        <div className="mt-6">
          <h2 className="text-2xl font-bold text-slate-900">Step 2: Upload Label Photo(s)</h2>
          <p className="text-base text-slate-500 mb-4">
            Take a photo or upload an image of the label (front and back, if needed).
          </p>
          <ImageDropzone files={files} onChange={setFiles} idPrefix="single" />
        </div>

        <button
          type="submit"
          disabled={!canSubmit}
          className="mt-6 w-full rounded-lg bg-[#15396a] px-6 py-4 text-lg font-bold text-white hover:bg-[#0b1f3a] disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {loading ? "Processing…" : "Step 3: Review Label"}
        </button>

        {error && <p className="mt-3 text-base text-red-700">{error}</p>}
      </form>

      <div>
        <h2 className="text-2xl font-bold text-slate-900 mb-4">Review Results</h2>

        {showStatusBar && (
          <div className="rounded-xl border border-slate-200 bg-white px-4 pt-4 pb-2 shadow-sm mb-4">
            <ProcessingStatusBar step={statusStep} done={isDone} />
          </div>
        )}

        {!showBar && !result && (
          <div className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center text-slate-400">
            Fill out the application details, upload a label image, and click
            "Review Label" to see results here.
          </div>
        )}

        {result && <ResultsPanel result={result} files={files} />}
      </div>
    </div>
  );
}
