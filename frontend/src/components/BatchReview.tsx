import { useRef, useState } from "react";
import { reviewLabelsBatch } from "../api";
import { downloadCsv } from "../csv";
import { EMPTY_APPLICATION, type ApplicationData, type ReviewResult } from "../types";
import ApplicationForm from "./ApplicationForm";
import ImageDropzone from "./ImageDropzone";
import ProcessingStatusBar from "./ProcessingStatusBar";
import ResultsPanel from "./ResultsPanel";
import StatusBadge from "./StatusBadge";

interface BatchItem {
  id: string;
  application: ApplicationData;
  files: File[];
}

function newItem(): BatchItem {
  return {
    id: crypto.randomUUID(),
    application: { ...EMPTY_APPLICATION },
    files: [],
  };
}

// Step indices for ProcessingStatusBar
// 0 = Uploading 1 = Reading label 2 = Checking 3 = Complete
const STEP_UPLOAD = 0;
const STEP_READING = 1;
const STEP_CHECKING = 2;
const STEP_COMPLETE = 3;

export default function BatchReview() {
  const [items, setItems] = useState<BatchItem[]>([newItem(), newItem()]);
  const [results, setResults] = useState<ReviewResult[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [statusStep, setStatusStep] = useState(-1);
  const [showBar, setShowBar] = useState(false);
  const stepTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  // Refs for auto-scroll
  const statusBarRef = useRef<HTMLDivElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);

  function clearTimers() {
    stepTimers.current.forEach(clearTimeout);
    stepTimers.current = [];
  }

  function startProgressTimers() {
    clearTimers();
    setStatusStep(STEP_UPLOAD);
    stepTimers.current.push(setTimeout(() => setStatusStep(STEP_READING), 1000));
    stepTimers.current.push(setTimeout(() => setStatusStep(STEP_CHECKING), 4000));
  }

  function updateItem(id: string, patch: Partial<BatchItem>) {
    setItems((prev) => prev.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }

  function removeItem(id: string) {
    setItems((prev) => prev.filter((item) => item.id !== id));
  }

  const canSubmit =
    items.length > 0 &&
    items.every(
      (item) =>
        item.files.length > 0 &&
        item.application.brand_name.trim() !== "" &&
        item.application.class_type.trim() !== "" &&
        item.application.alcohol_content.trim() !== "" &&
        item.application.net_contents.trim() !== ""
    ) &&
    !loading;

  function exportCsv() {
    if (!results) return;
    const rows: string[][] = [
      ["Files","Overall Status","Field","Field Status","Application Value","Label Value","Message"],
    ];
    results.forEach((result) => {
      const files = result.filenames.join(", ");
      if (result.error) {
        rows.push([files, "fail", "", "", "", "", result.error]);
        return;
      }
      result.fields.forEach((field) => {
        rows.push([files, result.overall_status, field.label_name, field.status,
          field.application_value ?? "", field.label_value ?? "", field.message]);
      });
    });
    downloadCsv("batch-review-results.csv", rows);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setShowBar(true);
    setError(null);
    setResults(null);
    setExpanded(null);
    startProgressTimers();
    // Scroll to status bar after a brief delay so it has rendered
    setTimeout(() => {
      statusBarRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
    try {
      const res = await reviewLabelsBatch(
        items.map((item) => ({ files: item.files, application: item.application }))
      );
      clearTimers();
      setStatusStep(STEP_COMPLETE);
      setResults(res);
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
      setLoading(false);
    }
  }

  const isDone = statusStep === STEP_COMPLETE;

  return (
    <div>
      <form onSubmit={handleSubmit} className="space-y-6">
        {items.map((item, index) => (
          <div key={item.id} className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-bold text-slate-900">Label {index + 1}</h2>
              {items.length > 1 && (
                <button type="button" className="text-sm font-semibold text-red-600 underline"
                  onClick={() => removeItem(item.id)}>Remove</button>
              )}
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <ApplicationForm value={item.application}
                onChange={(application) => updateItem(item.id, { application })}
                idPrefix={`batch-${item.id}`} />
              <ImageDropzone files={item.files}
                onChange={(files) => updateItem(item.id, { files })}
                idPrefix={`batch-${item.id}`} />
            </div>
          </div>
        ))}

        <div className="flex flex-wrap items-center gap-4">
          <button type="button"
            className="rounded-lg border-2 border-[#15396a] px-5 py-3 text-base font-bold text-[#15396a] hover:bg-blue-50"
            onClick={() => setItems((prev) => [...prev, newItem()])}>
            + Add Another Label
          </button>
          <button type="submit" disabled={!canSubmit}
            className="rounded-lg bg-[#15396a] px-6 py-3 text-lg font-bold text-white hover:bg-[#0b1f3a] disabled:cursor-not-allowed disabled:bg-slate-300">
            {loading ? "Processing…" : `Review ${items.length} Labels`}
          </button>
        </div>

        {error && <p className="text-base text-red-700">{error}</p>}
      </form>

      {/* Processing status bar - shown during and just after processing */}
      {showBar && (loading || isDone) && (
        <div ref={statusBarRef} className="mt-6 rounded-xl border border-slate-200 bg-white px-4 pt-4 pb-2 shadow-sm">
          <ProcessingStatusBar step={statusStep} done={isDone} />
        </div>
      )}

      {results && (
        <div ref={resultsRef} className="mt-10">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <h2 className="text-2xl font-bold text-slate-900">Batch Results</h2>
            <button type="button"
              className="rounded-lg border-2 border-[#15396a] px-4 py-2 text-sm font-bold text-[#15396a] hover:bg-blue-50"
              onClick={exportCsv}>Export CSV</button>
          </div>
          {/* Mobile: card stack. Desktop: table. */}
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
            {/* Desktop table header - hidden on mobile */}
            <div className="hidden sm:grid sm:grid-cols-[2fr_auto_2fr_auto] text-xs font-semibold uppercase tracking-wide text-slate-500 bg-slate-50 border-b border-slate-200">
              <div className="px-4 py-3">Files</div>
              <div className="px-4 py-3">Status</div>
              <div className="px-4 py-3">Issues</div>
              <div className="px-4 py-3"></div>
            </div>
            <div className="divide-y divide-slate-100">
              {results.map((result, index) => {
                const issues = result.fields.filter((f) => f.status !== "pass");
                const issueText = result.error
                  ? <span className="text-red-600">{result.error}</span>
                  : issues.length === 0 ? "No issues found"
                  : issues.map((f) => f.label_name).join(", ");
                return (
                  <div key={index}>
                    {/* Mobile card layout */}
                    <div className="sm:hidden p-4 space-y-2">
                      <p className="font-medium text-slate-800 text-sm break-all">{result.filenames.join(", ")}</p>
                      <div className="flex items-center gap-3">
                        <StatusBadge status={result.overall_status} size="sm" />
                      </div>
                      <p className="text-sm text-slate-600">{issueText}</p>
                      {!result.error && (
                        <button type="button"
                          className="w-full mt-1 rounded-lg border-2 border-blue-700 px-4 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-50"
                          onClick={() => setExpanded(expanded === index ? null : index)}>
                          {expanded === index ? "Hide details" : "View details"}
                        </button>
                      )}
                    </div>
                    {/* Desktop row layout */}
                    <div className="hidden sm:grid sm:grid-cols-[2fr_auto_2fr_auto] items-center">
                      <div className="px-4 py-3 font-medium text-slate-800 text-sm break-all">{result.filenames.join(", ")}</div>
                      <div className="px-4 py-3"><StatusBadge status={result.overall_status} size="sm" /></div>
                      <div className="px-4 py-3 text-sm text-slate-600">{issueText}</div>
                      <div className="px-4 py-3">
                        {!result.error && (
                          <button type="button"
                            className="text-sm font-semibold text-blue-700 underline whitespace-nowrap"
                            onClick={() => setExpanded(expanded === index ? null : index)}>
                            {expanded === index ? "Hide details" : "View details"}
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
          {expanded !== null && (
            <div className="mt-6">
              <ResultsPanel result={results[expanded]} files={items[expanded]?.files ?? []} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
