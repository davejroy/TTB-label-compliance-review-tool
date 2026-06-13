import { useEffect, useState } from "react";
import { reviewLabel } from "../api";
import { EMPTY_APPLICATION, type ApplicationData, type ReviewResult } from "../types";
import ApplicationForm from "./ApplicationForm";
import ImageDropzone from "./ImageDropzone";
import ResultsPanel from "./ResultsPanel";

const LOADING_STEPS = [
  "Uploading image…",
  "Reading label text…",
  "Checking compliance…",
  "Almost done…",
];

export default function SingleReview() {
  const [application, setApplication] = useState<ApplicationData>(EMPTY_APPLICATION);
  const [files, setFiles] = useState<File[]>([]);
  const [result, setResult] = useState<ReviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingStep, setLoadingStep] = useState(0);

  useEffect(() => {
    if (!loading) { setLoadingStep(0); return; }
    const interval = setInterval(() => {
      setLoadingStep((s) => (s + 1 < LOADING_STEPS.length ? s + 1 : s));
    }, 2500);
    return () => clearInterval(interval);
  }, [loading]);

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
    setError(null);
    setResult(null);
    try {
      const res = await reviewLabel(files, application);
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

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
          {loading ? LOADING_STEPS[loadingStep] : "Step 3: Review Label"}
        </button>

        {error && <p className="mt-3 text-base text-red-700">{error}</p>}
      </form>

      <div>
        <h2 className="text-2xl font-bold text-slate-900 mb-4">Review Results</h2>
        {loading && (
          <div className="rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
            <div className="flex flex-col items-center gap-4 text-slate-500">
              <svg className="animate-spin h-8 w-8 text-[#15396a]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
              </svg>
              <p className="text-base font-medium">{LOADING_STEPS[loadingStep]}</p>
              <p className="text-sm text-slate-400">Typically completes in 3–7 seconds</p>
            </div>
          </div>
        )}
        {!loading && !result && (
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
