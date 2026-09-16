import { useRef, useState } from "react";
import { reviewLabel } from "../api";
import {
  EMPTY_APPLICATION,
  type ApplicationData,
  type ReviewResult,
} from "../types";
import ApplicationForm from "./ApplicationForm";
import ImageDropzone from "./ImageDropzone";
import ProcessingStatusBar from "./ProcessingStatusBar";
import ResultsPanel from "./ResultsPanel";

// Step indices for ProcessingStatusBar
// 0 = Uploading  1 = Reading label  2 = Checking  3 = Complete
const STEP_UPLOAD = 0;
const STEP_READING = 1;
const STEP_CHECKING = 2;
const STEP_COMPLETE = 3;

/**
 * Build a minimal ApplicationData stub from label extracted values.
 * We only copy fields that Claude returned a non-empty string for — the rest
 * stay as the user's existing values so partial pre-fills don't erase real data.
 */
function mergeExtracted(
  current: ApplicationData,
  extracted: ReviewResult["extracted"],
): ApplicationData {
  const next: ApplicationData = { ...current };
  if (extracted.brand_name?.trim()) next.brand_name = extracted.brand_name.trim();
  if (extracted.class_type?.trim()) next.class_type = extracted.class_type.trim();
  if (extracted.alcohol_content?.trim()) next.alcohol_content = extracted.alcohol_content.trim();
  if (extracted.net_contents?.trim()) next.net_contents = extracted.net_contents.trim();
  if (extracted.name_and_address?.trim()) next.name_and_address = extracted.name_and_address.trim();
  if (extracted.country_of_origin?.trim()) next.country_of_origin = extracted.country_of_origin.trim();

  // Map beverage_type_guess to our enum — fall back to current if unrecognised
  const guess = extracted.beverage_type_guess?.toLowerCase() ?? "";
  if (guess.includes("distilled") || guess.includes("spirit")) {
    next.beverage_type = "distilled_spirits";
  } else if (guess.includes("wine")) {
    next.beverage_type = "wine";
  } else if (guess.includes("beer") || guess.includes("malt")) {
    next.beverage_type = "beer";
  }

  return next;
}

export default function SingleReview() {
  const [application, setApplication] = useState<ApplicationData>(EMPTY_APPLICATION);
  const [files, setFiles] = useState<File[]>([]);
  const [result, setResult] = useState<ReviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusStep, setStatusStep] = useState(-1);
  const [showBar, setShowBar] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  // Pre-fill state
  const [prefilling, setPrefilling] = useState(false);
  const [prefillError, setPrefillError] = useState<string | null>(null);
  const [prefillDone, setPrefillDone] = useState(false);

  // Submit guard refs to prevent double-invocations on rapid clicks
  const submittingRef = useRef(false);
  const stepTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  // Refs for auto-scroll
  const statusBarRef = useRef<HTMLDivElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const formTopRef = useRef<HTMLFormElement>(null);

  function clearTimers() {
    stepTimers.current.forEach(clearTimeout);
    stepTimers.current = [];
    setStatusMessage(null);
  }

  function startProgressTimers() {
    clearTimers();
    setStatusStep(STEP_UPLOAD);
    setStatusMessage("Uploading label…");

    // Advance to "Reading label" after ~1s (upload is fast)
    stepTimers.current.push(
      setTimeout(() => {
        setStatusStep(STEP_READING);
        setStatusMessage("Reading label text with Claude Vision…");
      }, 1000)
    );

    // Advance to "Checking" after ~4s
    stepTimers.current.push(
      setTimeout(() => {
        setStatusStep(STEP_CHECKING);
        setStatusMessage("Comparing label against COLA application details…");
      }, 4000)
    );

    // If still in flight after 10s (cold-start wake or heavy image)
    stepTimers.current.push(
      setTimeout(() => {
        setStatusMessage(
          "Server is waking up / processing high-resolution image… (first request may take ~15–30s)"
        );
      }, 10000)
    );

    // If still in flight after 20s
    stepTimers.current.push(
      setTimeout(() => {
        setStatusMessage("Still working… completing compliance review…");
      }, 20000)
    );
  }

  /** Reset all state back to blank and scroll to the top of the form. */
  function handleStartOver() {
    if (submittingRef.current || loading || prefilling) return;
    clearTimers();
    setApplication(EMPTY_APPLICATION);
    setFiles([]);
    setResult(null);
    setError(null);
    setStatusStep(-1);
    setShowBar(false);
    setPrefillDone(false);
    setPrefillError(null);
    setStatusMessage(null);
    setTimeout(() => {
      formTopRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
  }

  const canSubmit =
    files.length > 0 &&
    application.brand_name.trim() !== "" &&
    application.class_type.trim() !== "" &&
    application.alcohol_content.trim() !== "" &&
    application.net_contents.trim() !== "" &&
    !loading &&
    !prefilling;

  // Pre-fill: scan the uploaded photos to populate form fields automatically
  async function handlePrefill() {
    if (submittingRef.current || files.length === 0 || prefilling || loading) return;
    submittingRef.current = true;
    setPrefilling(true);
    setPrefillError(null);
    setPrefillDone(false);
    try {
      // Submit with current (possibly empty) application — we only care about extracted fields
      const res = await reviewLabel(files, application);
      setApplication((prev) => mergeExtracted(prev, res.extracted));
      setPrefillDone(true);
      // Clear the "Done" badge after 4 s
      setTimeout(() => setPrefillDone(false), 4000);
    } catch (err) {
      setPrefillError(
        err instanceof Error
          ? err.message
          : "Pre-fill failed — please fill the fields manually.",
      );
    } finally {
      submittingRef.current = false;
      setPrefilling(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submittingRef.current || loading || prefilling || !canSubmit) return;
    submittingRef.current = true;
    setLoading(true);
    setShowBar(true);
    setError(null);
    setResult(null);
    startProgressTimers();
    // Scroll to status bar after a brief delay so it has rendered
    setTimeout(() => {
      statusBarRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
    try {
      const res = await reviewLabel(files, application);
      clearTimers();
      setStatusStep(STEP_COMPLETE);
      setResult(res);
      // Scroll to results once they arrive
      setTimeout(() => {
        resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 100);
    } catch (err) {
      clearTimers();
      setError(err instanceof Error ? err.message : "Something went wrong.");
      setTimeout(() => {
        statusBarRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 100);
    } finally {
      submittingRef.current = false;
      setLoading(false);
    }
  }

  // Decide what to show in the results panel
  const showStatusBar = showBar && (loading || statusStep === STEP_COMPLETE);
  const isDone = statusStep === STEP_COMPLETE;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
      <form ref={formTopRef} onSubmit={handleSubmit} className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
        <h2 className="text-2xl font-bold text-slate-900">Step 1: Enter COLA Application Details</h2>
        <p className="text-base text-slate-500 mb-4">
          Type in the details exactly as they appear on the approved COLA application, or upload a
          label photo first and use <strong>Pre-fill from label</strong> to auto-populate the fields.
        </p>
        <ApplicationForm value={application} onChange={setApplication} idPrefix="single" />

        <div className="mt-6">
          <h2 className="text-2xl font-bold text-slate-900">Step 2: Upload Label Photo(s)</h2>
          <p className="text-base text-slate-500 mb-4">
            Take a photo or upload an image of the label (front and back, if needed).
          </p>
          <ImageDropzone files={files} onChange={setFiles} idPrefix="single" />
        </div>

        {/* Pre-fill button — shown once at least one image has been uploaded */}
        {files.length > 0 && (
          <div className="mt-4 flex flex-col gap-1">
            <button
              type="button"
              disabled={prefilling || loading}
              onClick={handlePrefill}
              className="w-full rounded-lg border-2 border-[#15396a] px-5 py-3 text-base font-semibold text-[#15396a] hover:bg-blue-50 disabled:cursor-not-allowed disabled:border-slate-300 disabled:text-slate-400 flex items-center justify-center gap-2"
            >
              {prefilling ? (
                <>
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                  </svg>
                  Scanning label…
                </>
              ) : prefillDone ? (
                <>✅ Fields pre-filled from label</>
              ) : (
                <>📷 Pre-fill fields from label photo</>
              )}
            </button>
            {prefillError && (
              <p className="text-sm text-red-600">{prefillError}</p>
            )}
            {prefillDone && (
              <p className="text-xs text-slate-500 text-center">
                Review the fields below — correct anything that was misread.
              </p>
            )}
          </div>
        )}

        <button
          type="submit"
          disabled={!canSubmit}
          className="mt-6 w-full rounded-lg bg-[#15396a] px-6 py-4 text-lg font-bold text-white hover:bg-[#0b1f3a] disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {loading ? "Processing…" : "Step 3: Review Label"}
        </button>

        {/* Start Over button — only shown after a review attempt */}
        {(result || error || showBar) && (
          <button
            type="button"
            disabled={loading || prefilling}
            onClick={handleStartOver}
            className="mt-3 w-full rounded-lg border border-slate-300 px-6 py-3 text-base font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            ↺ Start Over
          </button>
        )}

        {error && <p className="mt-3 text-base text-red-700">{error}</p>}
      </form>

      <div>
        <h2 className="text-2xl font-bold text-slate-900 mb-4">Review Results</h2>

        {showStatusBar && (
          <div
            ref={statusBarRef}
            className="rounded-xl border border-slate-200 bg-white px-4 pt-4 pb-2 shadow-sm mb-4"
          >
            <ProcessingStatusBar step={statusStep} done={isDone} statusMessage={loading ? statusMessage : null} />
          </div>
        )}

        {!showBar && !result && (
          <div className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center text-slate-400">
            Fill out the application details, upload a label image, and click
            "Review Label" to see results here.
          </div>
        )}

        {result && (
          <div ref={resultsRef}>
            <ResultsPanel result={result} files={files} />
          </div>
        )}
      </div>
    </div>
  );
}
