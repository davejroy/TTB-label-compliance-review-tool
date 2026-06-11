import { useState } from "react";
import { checkLabelsBatch } from "../api";
import { BEVERAGE_TYPE_LABELS, type LabelCheckResult } from "../types";
import ImageDropzone from "./ImageDropzone";
import LabelCheckResultsPanel from "./LabelCheckResultsPanel";
import StatusBadge from "./StatusBadge";

interface Item {
  id: string;
  files: File[];
}

function newItem(): Item {
  return { id: crypto.randomUUID(), files: [] };
}

export default function LabelOnlyCheck() {
  const [items, setItems] = useState<Item[]>([newItem()]);
  const [results, setResults] = useState<LabelCheckResult[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  function updateItem(id: string, files: File[]) {
    setItems((prev) => prev.map((item) => (item.id === id ? { ...item, files } : item)));
  }

  function removeItem(id: string) {
    setItems((prev) => prev.filter((item) => item.id !== id));
  }

  const canSubmit = items.length > 0 && items.every((item) => item.files.length > 0) && !loading;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResults(null);
    setExpanded(null);
    try {
      const res = await checkLabelsBatch(items.map((item) => ({ files: item.files })));
      setResults(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div className="mb-6 rounded-xl border border-blue-200 bg-blue-50 p-5">
        <h2 className="text-xl font-bold text-blue-900 mb-1">Label-Only Compliance Check</h2>
        <p className="text-base text-blue-800">
          Upload one or more label images to check them directly against TTB mandatory
          label requirements (27 CFR Parts 4, 5, 7, and 16) - no application data needed.
          The tool reads the brand name, class/type, alcohol content, net contents,
          bottler/importer name and address, country of origin, and the Government
          Warning statement, and flags anything that's missing or doesn't conform.
        </p>
      </div>

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
            <ImageDropzone
              files={item.files}
              onChange={(files) => updateItem(item.id, files)}
              idPrefix={`labelcheck-${item.id}`}
            />
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
            {loading
              ? `Checking ${items.length} label${items.length === 1 ? "" : "s"}...`
              : `Check ${items.length} Label${items.length === 1 ? "" : "s"}`}
          </button>
        </div>

        {error && <p className="text-base text-red-700">{error}</p>}
      </form>

      {results && (
        <div className="mt-10">
          <h2 className="text-2xl font-bold text-slate-900 mb-4">Results</h2>
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
            <table className="w-full text-left">
              <thead className="bg-slate-50 text-sm uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="p-4">Files</th>
                  <th className="p-4">Beverage Type</th>
                  <th className="p-4">Status</th>
                  <th className="p-4">Issues</th>
                  <th className="p-4"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {results.map((result, index) => {
                  const issues = result.checks.filter((c) => c.status !== "pass");
                  return (
                    <tr key={index}>
                      <td className="p-4 font-medium text-slate-800">
                        {result.filenames.join(", ")}
                      </td>
                      <td className="p-4 text-sm text-slate-600">
                        {result.beverage_type
                          ? BEVERAGE_TYPE_LABELS[result.beverage_type] ?? result.beverage_type
                          : "-"}
                      </td>
                      <td className="p-4">
                        <StatusBadge status={result.overall_status} size="sm" />
                      </td>
                      <td className="p-4 text-sm text-slate-600">
                        {issues.length === 0
                          ? "No issues found"
                          : issues.map((c) => c.label_name).join(", ")}
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
              <LabelCheckResultsPanel result={results[expanded]} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
