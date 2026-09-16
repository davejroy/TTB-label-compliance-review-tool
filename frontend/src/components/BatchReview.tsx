import { useRef, useState } from "react";
import { reviewLabelsBatchStream } from "../api";
import { downloadCsv } from "../csv";
import { EMPTY_APPLICATION, type ApplicationData, type ReviewResult } from "../types";
import ApplicationForm from "./ApplicationForm";
import ImageDropzone from "./ImageDropzone";
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

/** Status of an individual label in the streaming batch. */
type LabelStatus = "pending" | "processing" | "done" | "error";

interface LabelState {
  status: LabelStatus;
  result?: ReviewResult;
}

export default function BatchReview() {
  const [items, setItems] = useState<BatchItem[]>([newItem(), newItem()]);

  // labelStates tracks streaming progress per label — null means batch not started
  const [labelStates, setLabelStates] = useState<LabelState[] | null>(null);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  // Submit guard refs & timers
  const submittingRef = useRef(false);
  const stepTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  // Ref for auto-scroll
  const resultsRef = useRef<HTMLDivElement>(null);

  function clearTimers() {
    stepTimers.current.forEach(clearTimeout);
    stepTimers.current = [];
    setStatusMessage(null);
  }

  function startProgressTimers() {
    clearTimers();
    setStatusMessage("Starting batch review stream…");

    stepTimers.current.push(
      setTimeout(() => {
        setStatusMessage(
          "Server is waking up / analyzing batch images with Claude Vision… (first request may take ~15–30s)"
        );
      }, 10000)
    );

    stepTimers.current.push(
      setTimeout(() => {
        setStatusMessage("Still working… processing batch labels…");
      }, 25000)
    );
  }

  function updateItem(id: string, patch: Partial<BatchItem>) {
    if (loading) return;
    setItems((prev) => prev.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }

  function removeItem(id: string) {
    if (loading) return;
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
        item.application.net_contents.trim() !== "",
    ) &&
    !loading;

  // Derive completed results in order for CSV export
  const completedResults = labelStates
    ?.map((s) => s.result)
    .filter((r): r is ReviewResult => r !== undefined) ?? null;

  function exportCsv() {
    if (!completedResults?.length || loading) return;
    const rows: string[][] = [
      ["Files", "Overall Status", "Field", "Field Status", "Application Value", "Label Value", "Message"],
    ];
    completedResults.forEach((result) => {
      const files = result.filenames.join(", ");
      if (result.error) {
        rows.push([files, "fail", "", "", "", "", result.error]);
        return;
      }
      result.fields.forEach((field) => {
        rows.push([
          files,
          result.overall_status,
          field.label_name,
          field.status,
          field.application_value ?? "",
          field.label_value ?? "",
          field.message,
        ]);
      });
    });
    downloadCsv("batch-review-results.csv", rows);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submittingRef.current || loading || !canSubmit) return;
    submittingRef.current = true;
    setLoading(true);
    setError(null);
    setExpanded(null);
    startProgressTimers();

    // Initialise all labels as "pending"
    const initial: LabelState[] = items.map(() => ({ status: "pending" }));
    setLabelStates(initial);

    // Scroll to results panel after a brief delay
    setTimeout(() => {
      resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);

    try {
      await reviewLabelsBatchStream(
        items.map((item) => ({ files: item.files, application: item.application })),
        (index, result) => {
          setLabelStates((prev) => {
            if (!prev) return prev;
            const next = [...prev];
            next[index] = {
              status: result.error ? "error" : "done",
              result,
            };
            return next;
          });
        },
      );
      clearTimers();
    } catch (err) {
      clearTimers();
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      submittingRef.current = false;
      setLoading(false);
    }
  }

  // Count done labels for the progress header
  const doneCount = labelStates?.filter((s) => s.status === "done" || s.status === "error").length ?? 0;
  const totalCount = items.length;
  const allDone = labelStates !== null && doneCount === totalCount;

  return (
    <div>
      <form onSubmit={handleSubmit} className="space-y-6">
        {items.map((item, index) => (
          <div key={item.id} className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-bold text-slate-900">Label {index + 1}</h2>
              {items.length > 1 && (
                <button
                  type="button"
                  disabled={loading}
                  className="text-sm font-semibold text-red-600 underline disabled:opacity-50 disabled:cursor-not-allowed"
                  onClick={() => removeItem(item.id)}
                >
                  Remove
                </button>
              )}
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <ApplicationForm
                value={item.application}
                onChange={(application) => updateItem(item.id, { application })}
                idPrefix={`batch-${item.id}`}
              />
              <ImageDropzone
                files={item.files}
                onChange={(files) => updateItem(item.id, { files })}
                idPrefix={`batch-${item.id}`}
              />
            </div>
          </div>
        ))}

        <div className="flex flex-wrap items-center gap-4">
          <button
            type="button"
            disabled={loading}
            className="rounded-lg border-2 border-[#15396a] px-5 py-3 text-base font-bold text-[#15396a] hover:bg-blue-50 disabled:opacity-50 disabled:cursor-not-allowed"
            onClick={() => setItems((prev) => [...prev, newItem()])}
          >
            + Add Another Label
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded-lg bg-[#15396a] px-6 py-3 text-lg font-bold text-white hover:bg-[#0b1f3a] disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {loading ? `Processing… (${doneCount}/${totalCount})` : `Review ${items.length} Labels`}
          </button>
        </div>

        {error && <p className="mt-3 text-base text-red-700">{error}</p>}
      </form>

      {/* Streaming results panel */}
      {labelStates && (
        <div ref={resultsRef} className="mt-10">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <div>
              <h2 className="text-2xl font-bold text-slate-900">Batch Results</h2>
              {!allDone && (
                <p className="text-sm text-slate-500 mt-0.5">
                  {doneCount} of {totalCount} labels complete — results appear as each label
                  finishes…
                </p>
              )}
              {loading && statusMessage && (
                <div className="mt-2 flex items-center gap-2 rounded-lg bg-blue-50 px-3 py-2 text-sm font-medium text-slate-700 border border-blue-100 animate-pulse">
                  <svg className="h-4 w-4 animate-spin text-[#15396a] shrink-0" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                  </svg>
                  <span>{statusMessage}</span>
                </div>
              )}
            </div>
            {allDone && completedResults && completedResults.length > 0 && (
              <button
                type="button"
                disabled={loading}
                className="rounded-lg border-2 border-[#15396a] px-4 py-2 text-sm font-bold text-[#15396a] hover:bg-blue-50 disabled:opacity-50 disabled:cursor-not-allowed"
                onClick={exportCsv}
              >
                Export CSV
              </button>
            )}
          </div>

          {/* Per-label progress list */}
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
            {/* Desktop table header */}
            <div className="hidden sm:grid sm:grid-cols-[2fr_auto_2fr_auto] text-xs font-semibold uppercase tracking-wide text-slate-500 bg-slate-50 border-b border-slate-200">
              <div className="px-4 py-3">Files</div>
              <div className="px-4 py-3">Status</div>
              <div className="px-4 py-3">Issues</div>
              <div className="px-4 py-3" />
            </div>

            <div className="divide-y divide-slate-100">
              {labelStates.map((state, index) => {
                const result = state.result;
                const isPending = state.status === "pending";

                // Pending label — show a spinner row
                if (isPending || !result) {
                  return (
                    <div key={index} className="p-4 flex items-center gap-3 text-slate-400">
                      {isPending ? (
                        <>
                          <svg className="animate-spin h-4 w-4 text-[#15396a]" viewBox="0 0 24 24" fill="none">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                          </svg>
                          <span className="text-sm">Label {index + 1} — processing…</span>
                        </>
                      ) : (
                        <span className="text-sm">Label {index + 1} — waiting…</span>
                      )}
                    </div>
                  );
                }

                const issues = result.fields?.filter((f) => f.status !== "pass") ?? [];
                const issueText = result.error ? (
                  <span className="text-red-600">{result.error}</span>
                ) : issues.length === 0 ? (
                  "No issues found"
                ) : (
                  issues.map((f) => f.label_name).join(", ")
                );

                return (
                  <div key={index}>
                    {/* Mobile card */}
                    <div className="sm:hidden p-4 space-y-2">
                      <p className="font-medium text-slate-800 text-sm break-all">
                        {result.filenames.join(", ")}
                      </p>
                      <div className="flex items-center gap-3">
                        <StatusBadge status={result.overall_status} size="sm" />
                        {typeof result.processing_time_ms === "number" && result.processing_time_ms > 0 && (
                          <span className="text-xs text-slate-400">
                            Processed in {(result.processing_time_ms / 1000).toFixed(1)}s
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-slate-600">{issueText}</p>
                      {!result.error && (
                        <button
                          type="button"
                          className="text-sm font-semibold text-[#15396a] underline"
                          onClick={() => setExpanded(expanded === index ? null : index)}
                        >
                          {expanded === index ? "Hide details" : "View details"}
                        </button>
                      )}
                    </div>

                    {/* Desktop row */}
                    <div className="hidden sm:grid sm:grid-cols-[2fr_auto_2fr_auto] items-center">
                      <div className="px-4 py-3 text-sm font-medium text-slate-800 break-all">
                        {result.filenames.join(", ")}
                      </div>
                      <div className="px-4 py-3">
                        <StatusBadge status={result.overall_status} size="sm" />
                      </div>
                      <div className="px-4 py-3 text-sm text-slate-600">
                        <div>{issueText}</div>
                        {typeof result.processing_time_ms === "number" && result.processing_time_ms > 0 && (
                          <div className="text-xs text-slate-400 mt-0.5">
                            Processed in {(result.processing_time_ms / 1000).toFixed(1)}s
                          </div>
                        )}
                      </div>
                      <div className="px-4 py-3">
                        {!result.error && (
                          <button
                            type="button"
                            className="text-sm font-semibold text-[#15396a] underline"
                            onClick={() => setExpanded(expanded === index ? null : index)}
                          >
                            {expanded === index ? "Hide" : "Details"}
                          </button>
                        )}
                      </div>
                    </div>

                    {expanded === index && !result.error && (
                      <div className="px-4 pb-4">
                        <ResultsPanel result={result} files={items[index]?.files ?? []} />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
