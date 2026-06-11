import { useState } from "react";
import { BEVERAGE_TYPE_LABELS, type LabelCheckResult } from "../types";
import ConfidenceBadge from "./ConfidenceBadge";
import LabelImageViewer from "./LabelImageViewer";
import StatusBadge from "./StatusBadge";

/**
 * Renders one Label-Only Check result: the label image viewer (with
 * field-location highlighting), the detected beverage type, and the
 * Pass/Needs Review/Fail breakdown for each TTB requirement.
 */
export default function LabelCheckResultsPanel({
  result,
  files,
}: {
  result: LabelCheckResult;
  files: File[];
}) {
  const [highlightedField, setHighlightedField] = useState<string | null>(null);

  if (result.error) {
    return (
      <div className="rounded-xl border border-red-300 bg-red-50 p-6">
        <h3 className="text-xl font-bold text-red-800 mb-2">Could not process label</h3>
        <p className="text-red-700">{result.error}</p>
      </div>
    );
  }

  const beverageLabel = result.beverage_type
    ? BEVERAGE_TYPE_LABELS[result.beverage_type] ?? result.beverage_type
    : "Unknown";

  return (
    <div className="space-y-6">
      {files.length > 0 && (
        <LabelImageViewer
          files={files}
          fieldLocations={result.extracted.field_locations}
          highlightedField={highlightedField}
        />
      )}

      <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-5">
          <div>
            <h3 className="text-xl font-bold text-slate-900">{result.filenames.join(", ")}</h3>
            <p className="text-sm text-slate-500">
              Detected as {beverageLabel} &middot; Processed in{" "}
              {(result.processing_time_ms / 1000).toFixed(1)}s
            </p>
          </div>
          <StatusBadge status={result.overall_status} size="lg" />
        </div>

        <div className="divide-y divide-slate-100">
          {result.checks.map((check) => {
            const location = result.extracted.field_locations.find(
              (loc) => loc.field === check.field
            );
            return (
              <div
                key={check.field}
                className={`p-5 transition-colors ${
                  highlightedField === check.field ? "bg-blue-50" : ""
                } ${files.length > 0 ? "cursor-pointer" : ""}`}
                onMouseEnter={() => files.length > 0 && setHighlightedField(check.field)}
                onMouseLeave={() => setHighlightedField(null)}
                onClick={() =>
                  files.length > 0 &&
                  setHighlightedField((current) => (current === check.field ? null : check.field))
                }
              >
                <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                  <h4 className="text-lg font-semibold text-slate-800">{check.label_name}</h4>
                  <div className="flex items-center gap-2">
                    {location && <ConfidenceBadge confidence={location.confidence} />}
                    <StatusBadge status={check.status} size="sm" />
                  </div>
                </div>
                <p className="text-base text-slate-600 mb-3">{check.message}</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="rounded-lg bg-slate-50 p-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-1">
                      TTB Requirement
                    </p>
                    <p className="text-sm text-slate-800 whitespace-pre-wrap break-words">
                      {check.application_value}
                    </p>
                  </div>
                  <div className="rounded-lg bg-slate-50 p-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-1">
                      Found on Label
                    </p>
                    <p className="text-sm font-mono text-slate-800 whitespace-pre-wrap break-words">
                      {check.label_value || <span className="text-slate-400">(not found)</span>}
                    </p>
                  </div>
                </div>
              </div>
            );
          })}
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
    </div>
  );
}
