import { useEffect, useRef, useState } from "react";
import { checkLabelsBatch } from "../api";
import { downloadCsv } from "../csv";
import { BEVERAGE_TYPE_LABELS, type LabelCheckResult } from "../types";
import ImageDropzone from "./ImageDropzone";
import LabelCheckResultsPanel from "./LabelCheckResultsPanel";
import ProcessingStatusBar from "./ProcessingStatusBar";
import StatusBadge from "./StatusBadge";

interface Item {
  id: string;
  files: File[];
}

function newItem(): Item {
  return { id: crypto.randomUUID(), files: [] };
}

// Step indices for ProcessingStatusBar
const STEP_UPLOAD = 0;
const STEP_READING = 1;
const STEP_CHECKING = 2;
const STEP_COMPLETE = 3;

/**
 * Modal dialog that asks the agent to confirm the beverage type when Claude
 * could not determine it from the label text alone.  Appears automatically
 * when needs_beverage_confirmation=true is returned from the API.
 */
function BeverageTypeDialog({
  guessedType,
  labelFiles,
  onConfirm,
  onDismiss,
}: {
  guessedType: string | undefined;
  labelFiles: File[];
  onConfirm: (type: string) => void;
  onDismiss: () => void;
}) {
  const [selected, setSelected] = useState<string>(
    guessedType && guessedType in BEVERAGE_TYPE_LABELS ? guessedType : "distilled_spirits"
  );
  const fileNames = labelFiles.map((f) => f.name).join(", ");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="relative mx-4 max-w-md w-full rounded-2xl bg-white shadow-2xl p-6">
        <h3 className="text-lg font-bold text-slate-900 mb-2">Confirm Beverage Type</h3>
        <p className="text-sm text-slate-600 mb-1">
          <span className="font-medium">Label:</span>{" "}
          <span className="break-all">{fileNames || "unknown"}</span>
        </p>
        <p className="text-sm text-slate-600 mb-4">
          Claude could not determine the beverage type from the label text alone.
          Please select the correct type so the appropriate TTB requirements are applied.
          {guessedType && guessedType !== "unknown" && (
            <span className="block mt-1 text-blue-700 font-medium">
              {"Claude\u2019s best guess: " + (BEVERAGE_TYPE_LABELS[guessedType] ?? guessedType)}
            </span>
          )}
        </p>
        <div className="space-y-2 mb-6">
          {Object.entries(BEVERAGE_TYPE_LABELS)
            .filter(([key]) => key !== "unknown")
            .map(([key, label]) => (
              <label
                key={key}
                className={`flex items-center gap-3 cursor-pointer rounded-lg border-2 px-4 py-3 transition-colors ${
                  selected === key
                    ? "border-blue-600 bg-blue-50 text-blue-900"
                    : "border-slate-200 hover:border-blue-300 text-slate-800"
                }`}
              >
                <input
                  type="radio"
                  name="beverage_type_confirm"
                  value={key}
                  checked={selected === key}
                  onChange={() => setSelected(key)}
                  className="accent-blue-600"
                />
                <span className="font-medium">{label}</span>
              </label>
            ))}
        </div>
        <div className="flex gap-3">
          <button
            type="button"
            className="flex-1 rounded-lg border-2 border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
            onClick={onDismiss}
          >
            Skip (keep unconfirmed)
          </button>
          <button
            type="button"
            className="flex-1 rounded-lg bg-[#15396a] px-4 py-2 text-sm font-bold text-white hover:bg-[#1a4a8a]"
            onClick={() => onConfirm(selected)}
          >
            Confirm &amp; Re-check
          </button>
        </div>
      </div>
    </div>
  );
}

export default function LabelOnlyCheck() {
  const [items, setItems] = useState<Item[]>([newItem()]);
  const [results, setResults] = useState<LabelCheckResult[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [statusStep, setStatusStep] = useState<number>(STEP_UPLOAD);
  const [showBar, setShowBar] = useState(false);
  const stepTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const statusBarRef = useRef<HTMLDivElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const label1BoxRef = useRef<HTMLDivElement>(null);

  /**
   * Index of the result currently waiting for beverage-type confirmation.
   * null when no confirmation is pending.
   */
  const [pendingConfirmIdx, setPendingConfirmIdx] = useState<number | null>(null);

  // Scroll on mount so the bottom of Label 1 image box is in view
  useEffect(() => {
    if (label1BoxRef.current) {
      const el = label1BoxRef.current;
      const rect = el.getBoundingClientRect();
      const scrollBy = rect.bottom - window.innerHeight + 24;
      if (scrollBy > 0) window.scrollBy({ top: scrollBy, behavior: "smooth" });
    }
  }, []);

  const isDone = !loading && results !== null;

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

  function updateItem(id: string, files: File[]) {
    setItems((prev) => prev.map((item) => (item.id === id ? { ...item, files } : item)));
  }

  function removeItem(id: string) {
    setItems((prev) => (prev.length > 1 ? prev.filter((item) => item.id !== id) : prev));
  }

  const canSubmit = !loading && items.some((item) => item.files.length > 0);

  function exportCsv() {
    if (!results) return;
    const rows: string[][] = [
      ["Files","Beverage Type","Overall Status","Check","Check Status","TTB Requirement","Label Value","Notes"],
    ];
    results.forEach((result) => {
      const files = result.filenames.join("; ");
      const beverageType = result.beverage_type
        ? BEVERAGE_TYPE_LABELS[result.beverage_type] ?? result.beverage_type
        : "";
      if (result.error) {
        rows.push([files, beverageType, "fail", "", "", "", "", result.error]);
        return;
      }
      result.checks.forEach((check) => {
        rows.push([files, beverageType, result.overall_status, check.label_name,
          check.status, check.application_value ?? "", check.label_value ?? "", check.message]);
      });
    });
    downloadCsv("label-check-results.csv", rows);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setShowBar(true);
    setError(null);
    setResults(null);
    setExpanded(null);
    setPendingConfirmIdx(null);
    startProgressTimers();
    setTimeout(() => {
      statusBarRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }, 50);
    try {
      const res = await checkLabelsBatch(items.map((item) => ({ files: item.files })));
      clearTimers();
      setStatusStep(STEP_COMPLETE);
      setResults(res);
      // Queue the first result that needs beverage-type confirmation
      const firstPending = res.findIndex((r) => r.needs_beverage_confirmation);
      if (firstPending !== -1) setPendingConfirmIdx(firstPending);
      setTimeout(() => {
        resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 100);
    } catch (err) {
      clearTimers();
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
      setTimeout(() => {
        statusBarRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 100);
    } finally {
      setLoading(false);
    }
  }

  /** Re-run the check for one label with an agent-confirmed beverage type. */
  async function confirmBeverageType(idx: number, beverageType: string) {
    setPendingConfirmIdx(null);
    if (!results) return;
    setLoading(true);
    setError(null);
    startProgressTimers();
    try {
      const item = items[idx];
      if (!item) return;
      const [confirmed] = await checkLabelsBatch([{ files: item.files }], beverageType);
      clearTimers();
      setStatusStep(STEP_COMPLETE);
      setResults((prev) => {
        if (!prev) return prev;
        const updated = [...prev];
        updated[idx] = confirmed;
        return updated;
      });
      // Check for more pending confirmations after this one
      const nextPending = results.findIndex((r, i) => i > idx && r.needs_beverage_confirmation);
      if (nextPending !== -1) setPendingConfirmIdx(nextPending);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Re-check failed. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div className="mb-6 rounded-xl border border-blue-200 bg-blue-50 px-6 py-4">
        <h2 className="text-xl font-bold text-blue-900 mb-1">Label-Only Check</h2>
        <p className="text-base text-blue-800">
          Upload one or more label images to check them directly against the TTB mandatory
          label requirements (27 CFR Parts 4, 5, 7, and 16) - no application data required.
          The tool reads the brand name, class/type, alcohol content, net contents,
          bottler/importer name and address, country of origin, and the Government
          Warning statement, and flags anything that&apos;s missing or doesn&apos;t match the required text.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        {items.map((item, index) => (
          <div key={item.id} ref={index === 0 ? label1BoxRef : undefined}
               className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-bold text-slate-900">Label {index + 1}</h2>
              {items.length > 1 && (
                <button type="button" className="text-sm font-semibold text-red-600 hover:text-red-800"
                  onClick={() => removeItem(item.id)}>Remove</button>
              )}
            </div>
            <ImageDropzone files={item.files} onChange={(files) => updateItem(item.id, files)}
              idPrefix={`labelcheck-${item.id}`} />
          </div>
        ))}

        <div className="flex flex-wrap items-center gap-4">
          <button type="button"
            className="rounded-lg border-2 border-[#15396a] px-5 py-3 text-base font-semibold text-[#15396a] hover:bg-blue-50"
            onClick={() => setItems((prev) => [...prev, newItem()])}>
            + Add Another Label
          </button>
          <button type="submit" disabled={!canSubmit}
            className="rounded-lg bg-[#15396a] px-6 py-3 text-lg font-bold text-white hover:bg-[#1a4a8a] disabled:opacity-50 disabled:cursor-not-allowed">
            {loading ? "Processing…" : `Check ${items.length} Label${items.length !== 1 ? "s" : ""}`}
          </button>
        </div>

        {error && (
          <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{error}</div>
        )}
      </form>

      {showBar && (loading || isDone) && (
        <div ref={statusBarRef} className="mt-6 rounded-xl border border-slate-200 bg-white shadow-sm p-4">
          <ProcessingStatusBar step={statusStep} done={isDone} />
        </div>
      )}

      {/* Beverage-type confirmation dialog - shown when Claude cannot determine the type */}
      {pendingConfirmIdx !== null && results && (
        <BeverageTypeDialog
          guessedType={results[pendingConfirmIdx]?.beverage_type}
          labelFiles={items[pendingConfirmIdx]?.files ?? []}
          onConfirm={(type) => confirmBeverageType(pendingConfirmIdx, type)}
          onDismiss={() => setPendingConfirmIdx(null)}
        />
      )}

      {results && (
        <div ref={resultsRef} className="mt-6">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <h2 className="text-2xl font-bold text-slate-900">Results</h2>
            <button type="button"
              className="rounded-lg border-2 border-[#15396a] px-4 py-2 text-sm font-semibold text-[#15396a] hover:bg-blue-50"
              onClick={exportCsv}>Export CSV</button>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
            <div className="hidden sm:grid sm:grid-cols-[2fr_1fr_auto_2fr_auto] border-b border-slate-200 bg-slate-50 text-sm font-semibold text-slate-700">
              <div className="px-4 py-3">Files</div>
              <div className="px-4 py-3">Beverage Type</div>
              <div className="px-4 py-3">Status</div>
              <div className="px-4 py-3">Issues</div>
              <div className="px-4 py-3"></div>
            </div>
            <div className="divide-y divide-slate-100">
              {results.map((result, index) => {
                const issues = result.checks?.filter((c) => c.status !== "pass") ?? [];
                const issueText = result.error
                  ? <span className="text-red-600">{result.error}</span>
                  : issues.length === 0 ? "No issues found"
                  : issues.map((c) => c.label_name).join(", ");
                const beverageLabel = result.beverage_type
                  ? BEVERAGE_TYPE_LABELS[result.beverage_type] ?? result.beverage_type
                  : "-";
                const needsConfirm = result.needs_beverage_confirmation;
                return (
                  <div key={index}>
                    <div className="sm:hidden p-4 space-y-2">
                      <p className="font-medium text-slate-800 text-sm break-all">
                        {result.filenames.join(", ")}
                      </p>
                      <div className="flex items-center gap-3 flex-wrap">
                        <StatusBadge status={result.overall_status} size="sm" />
                        <span className="text-xs text-slate-500">{beverageLabel}</span>
                        {needsConfirm && (
                          <button type="button"
                            className="text-xs font-semibold text-amber-700 underline"
                            onClick={() => setPendingConfirmIdx(index)}>
                            Confirm type
                          </button>
                        )}
                      </div>
                      <p className="text-sm text-slate-600">{issueText}</p>
                      {!result.error && (
                        <button type="button"
                          className="w-full mt-1 rounded-lg border-2 border-blue-700 px-3 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-50"
                          onClick={() => setExpanded(expanded === index ? null : index)}>
                          {expanded === index ? "Hide details" : "View details"}
                        </button>
                      )}
                    </div>
                    <div className="hidden sm:grid sm:grid-cols-[2fr_1fr_auto_2fr_auto] items-center">
                      <div className="px-4 py-3 font-medium text-slate-800 text-sm break-all">
                        {result.filenames.join(", ")}
                      </div>
                      <div className="px-4 py-3 text-sm text-slate-600">
                        {beverageLabel}
                        {needsConfirm && (
                          <button type="button"
                            className="block mt-1 text-xs font-semibold text-amber-700 underline"
                            onClick={() => setPendingConfirmIdx(index)}>
                            Confirm type
                          </button>
                        )}
                      </div>
                      <div className="px-4 py-3"><StatusBadge status={result.overall_status} size="sm" /></div>
                      <div className="px-4 py-3 text-sm text-slate-600">{issueText}</div>
                      <div className="px-4 py-3">
                        {!result.error && (
                          <button type="button"
                            className="text-sm font-semibold text-blue-700 underline hover:text-blue-900"
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
          {expanded !== null && results[expanded] && (
            <div className="mt-6">
              <LabelCheckResultsPanel result={results[expanded]} files={items[expanded]?.files ?? []} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
