import { useEffect, useState } from "react";
import { reviewLabelsBatch } from "../api";
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

const LOADING_STEPS = [
  "Uploading images…",
  "Reading label text…",
  "Checking compliance…",
  "Almost done…",
];

export default function BatchReview() {
  const [items, setItems] = useState<BatchItem[]>([newItem(), newItem()]);
  const [results, setResults] = useState<ReviewResult[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [loadingStep, setLoadingStep] = useState(0);

  useEffect(() => {
    if (!loading) { setLoadingStep(0); return; }
    const interval = setInterval(() => {
      setLoadingStep((s) => (s + 1 < LOADING_STEPS.length ? s + 1 : s));
    }, 2500);
    return () => clearInterval(interval);
  }, [loading]);

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
      [
        "Files",
        "Overall Status",
        "Field",
        "Field Status",
        "Application Value",
        "Label Value",
        "Message",
      ],
    ];
    results.forEach((result) => {
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
    setLoading(true);
    setError(null);
    setResults(null);
    setExpanded(null);
    try {
      const res = await reviewLabelsBatch(
        items.map((item) => ({ files: item.files, application: item.application }))
      );
      setResults(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

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
                  className="text-sm font-semibold text-red-600 underline"
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
            className="rounded-lg border-2 border-[#15396a] px-5 py-3 text-base font-bold text-[#15396a] hover:bg-blue-50"
            onClick={() => setItems((prev) => [...prev, newItem()])}
          >
            + Add Another Label
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded-lg bg-[#15396a] px-6 py-3 text-lg font-bold text-white hover:bg-[#0b1f3a] disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {loading ? LOADING_STEPS[loadingStep] : `Review ${items.length} Labels`}
          </button>
        </div>

        {error && <p className="text-base text-red-700">{error}</p>}
      </form>

      {loading && (
        <div className="mt-8 rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
          <div className="flex flex-col items-center gap-4 text-slate-500">
            <svg className="animate-spin h-8 w-8 text-[#15396a]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
            </svg>
            <p className="text-base font-medium">{LOADING_STEPS[loadingStep]}</p>
            <p className="text-sm text-slate-400">
              Reviewing {items.length} labels — typically completes in 3–7 seconds each
            </p>
          </div>
        </div>
      )}

      {results && (
        <div className="mt-10">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <h2 className="text-2xl font-bold text-slate-900">Batch Results</h2>
            <button
              type="button"
              className="rounded-lg border-2 border-[#15396a] px-4 py-2 text-sm font-bold text-[#15396a] hover:bg-blue-50"
              onClick={exportCsv}
            >
              Export CSV
            </button>
          </div>
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
            <table className="w-full text-left">
              <thead className="bg-slate-50 text-sm uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="p-4">Files</th>
                  <th className="p-4">Status</th>
                  <th className="p-4">Issues</th>
                  <th className="p-4"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {results.map((result, index) => {
                  const issues = result.fields.filter((f) => f.status !== "pass");
                  return (
                    <tr key={index}>
                      <td className="p-4 font-medium text-slate-800">
                        {result.filenames.join(", ")}
                      </td>
                      <td className="p-4">
                        <StatusBadge status={result.overall_status} size="sm" />
                      </td>
                      <td className="p-4 text-sm text-slate-600">
                        {result.error
                          ? <span className="text-red-600">{result.error}</span>
                          : issues.length === 0
                            ? "No issues found"
                            : issues.map((f) => f.label_name).join(", ")}
                      </td>
                      <td className="p-4">
                        {!result.error && (
                          <button
                            type="button"
                            className="text-sm font-semibold text-blue-700 underline"
                            onClick={() => setExpanded(expanded === index ? null : index)}
                          >
                            {expanded === index ? "Hide details" : "View details"}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
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
