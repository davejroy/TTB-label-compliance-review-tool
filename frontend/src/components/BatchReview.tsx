import { useState } from "react";
import { reviewLabelsBatch } from "../api";
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

export default function BatchReview() {
  const [items, setItems] = useState<BatchItem[]>([newItem(), newItem()]);
  const [results, setResults] = useState<ReviewResult[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

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
            className="rounded-lg border-2 border-blue-600 px-5 py-3 text-base font-bold text-blue-700 hover:bg-blue-50"
            onClick={() => setItems((prev) => [...prev, newItem()])}
          >
            + Add Another Label
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded-lg bg-blue-600 px-6 py-3 text-lg font-bold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {loading ? `Reviewing ${items.length} labels...` : `Review ${items.length} Labels`}
          </button>
        </div>

        {error && <p className="text-base text-red-700">{error}</p>}
      </form>

      {results && (
        <div className="mt-10">
          <h2 className="text-2xl font-bold text-slate-900 mb-4">Batch Results</h2>
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
                        {issues.length === 0
                          ? "No issues found"
                          : issues.map((f) => f.label_name).join(", ")}
                      </td>
                      <td className="p-4">
                        <button
                          type="button"
                          className="text-sm font-semibold text-blue-700 underline"
                          onClick={() => setExpanded(expanded === index ? null : index)}
                        >
                          {expanded === index ? "Hide details" : "View details"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {expanded !== null && (
            <div className="mt-6">
              <ResultsPanel result={results[expanded]} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
