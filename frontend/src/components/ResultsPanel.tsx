import type { ReviewResult } from "../types";
import StatusBadge from "./StatusBadge";

export default function ResultsPanel({ result }: { result: ReviewResult }) {
  if (result.error) {
    return (
      <div className="rounded-xl border border-red-300 bg-red-50 p-6">
        <h3 className="text-xl font-bold text-red-800 mb-2">Could not process label</h3>
        <p className="text-red-700">{result.error}</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-5">
        <div>
          <h3 className="text-xl font-bold text-slate-900">{result.filename}</h3>
          <p className="text-sm text-slate-500">
            Processed in {(result.processing_time_ms / 1000).toFixed(1)}s
          </p>
        </div>
        <StatusBadge status={result.overall_status} size="lg" />
      </div>

      <div className="divide-y divide-slate-100">
        {result.fields.map((field) => (
          <div key={field.field} className="p-5">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
              <h4 className="text-lg font-semibold text-slate-800">{field.label_name}</h4>
              <StatusBadge status={field.status} size="sm" />
            </div>
            <p className="text-base text-slate-600 mb-3">{field.message}</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="rounded-lg bg-slate-50 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-1">
                  Application
                </p>
                <p className="text-sm font-mono text-slate-800 whitespace-pre-wrap break-words">
                  {field.application_value || <span className="text-slate-400">(none)</span>}
                </p>
              </div>
              <div className="rounded-lg bg-slate-50 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-1">
                  Label
                </p>
                <p className="text-sm font-mono text-slate-800 whitespace-pre-wrap break-words">
                  {field.label_value || <span className="text-slate-400">(not found)</span>}
                </p>
              </div>
            </div>
          </div>
        ))}
      </div>

      {result.extracted.notes && (
        <div className="border-t border-slate-200 bg-amber-50 p-4">
          <p className="text-sm text-amber-800">
            <span className="font-semibold">Image notes: </span>
            {result.extracted.notes}
          </p>
        </div>
      )}
    </div>
  );
}
