import { useState } from "react";
import type { ReviewResult } from "../types";
import ConfidenceBadge from "./ConfidenceBadge";
import LabelImageViewer from "./LabelImageViewer";
import StatusBadge from "./StatusBadge";
import TypeSizeDetailsView from "./TypeSizeDetailsView";

/**
 * How many low-confidence fields trigger the "retake photo" banner.
 * Even one low-confidence read is worth surfacing, but we only show
 * the full tips panel when there are at least this many.
 */
const LOW_CONFIDENCE_THRESHOLD = 1;

/** Photo tips shown when one or more fields have low read confidence. */
const PHOTO_TIPS = [
  "Ensure the label is flat and fully unrolled — curved labels distort text.",
  "Shoot in bright, even light; avoid shadows and direct flash glare.",
  "Hold the camera parallel to the label — avoid shooting at an angle.",
  "Crop tightly: fill the frame with the label, not the bottle.",
  "For dark labels, use a white card or white surface as a reflector behind you.",
  "If text is blurry, tap the label area on your phone screen to focus before capturing.",
  "For small print (net contents, ABV), zoom in and take a separate close-up shot.",
];

export default function ResultsPanel({ result, files }: { result: ReviewResult; files: File[] }) {
  const [highlightedField, setHighlightedField] = useState<string | null>(null);
  const [tipsExpanded, setTipsExpanded] = useState(false);

  if (result.error) {
    // Detect low-quality image errors and show targeted tips
    const isImageQualityError =
      result.error.toLowerCase().includes("confidence") ||
      result.error.toLowerCase().includes("low quality") ||
      result.error.toLowerCase().includes("unable to read") ||
      result.error.toLowerCase().includes("blurr");

    return (
      <div className="rounded-xl border border-red-300 bg-red-50 p-6 space-y-4">
        <h3 className="text-xl font-bold text-red-800 mb-2">Could not process label</h3>
        <p className="text-red-700">{result.error}</p>

        {isImageQualityError && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-4">
            <h4 className="text-base font-semibold text-amber-800 mb-2">
              📷 Tips for a better photo
            </h4>
            <ul className="list-disc list-inside text-sm text-amber-900 space-y-1">
              {PHOTO_TIPS.map((tip) => (
                <li key={tip}>{tip}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  // Count low-confidence field reads
  const lowConfidenceFields = result.extracted.field_locations.filter(
    (loc) => loc.confidence === "low",
  );
  const hasLowConfidence = lowConfidenceFields.length >= LOW_CONFIDENCE_THRESHOLD;

  return (
    <div className="space-y-6">
      {files.length > 0 && (
        <LabelImageViewer
          files={files}
          fieldLocations={result.extracted.field_locations}
          highlightedField={highlightedField}
        />
      )}

      {/* Low-confidence retake banner */}
      {hasLowConfidence && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h4 className="text-base font-semibold text-amber-800">
                ⚠️ {lowConfidenceFields.length} field
                {lowConfidenceFields.length > 1 ? "s were" : " was"} read with low confidence
              </h4>
              <p className="text-sm text-amber-700 mt-0.5">
                Results may be inaccurate.{" "}
                <strong>
                  {lowConfidenceFields.map((l) => l.field.replace(/_/g, " ")).join(", ")}
                </strong>{" "}
                could not be read clearly. Consider retaking the photo.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setTipsExpanded((v) => !v)}
              className="shrink-0 text-sm font-semibold text-amber-800 underline"
            >
              {tipsExpanded ? "Hide tips" : "Photo tips ▾"}
            </button>
          </div>

          {tipsExpanded && (
            <ul className="mt-3 list-disc list-inside text-sm text-amber-900 space-y-1 border-t border-amber-200 pt-3">
              {PHOTO_TIPS.map((tip) => (
                <li key={tip}>{tip}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-5">
        <div>
          <h3 className="text-xl font-bold text-slate-900">{result.filenames.join(", ")}</h3>
          {typeof result.processing_time_ms === "number" && result.processing_time_ms > 0 && (
            <p className="text-sm text-slate-500">
              Processed in {(result.processing_time_ms / 1000).toFixed(1)}s
            </p>
          )}
        </div>
          <StatusBadge status={result.overall_status} size="lg" />
        </div>

        <div className="divide-y divide-slate-100">
          {result.fields.map((field) => {
            const location = result.extracted.field_locations.find(
              (loc) => loc.field === field.field,
            );
            const isLowConf = location?.confidence === "low";
            return (
              <div
                key={field.field}
                className={`p-5 transition-colors ${
                  highlightedField === field.field ? "bg-blue-50" : ""
                } ${isLowConf ? "border-l-4 border-l-amber-400" : ""} ${files.length > 0 ? "cursor-pointer" : ""}`}
                onMouseEnter={() => files.length > 0 && setHighlightedField(field.field)}
                onMouseLeave={() => setHighlightedField(null)}
                onClick={() =>
                  files.length > 0 &&
                  setHighlightedField((current) =>
                    current === field.field ? null : field.field,
                  )
                }
              >
                <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                  <h4 className="text-lg font-semibold text-slate-800">{field.label_name}</h4>
                  <div className="flex items-center gap-2">
                    {location && <ConfidenceBadge confidence={location.confidence} />}
                    <StatusBadge status={field.status} size="sm" />
                  </div>
                </div>
                <p className="text-base text-slate-600 mb-3">{field.message}</p>
                {isLowConf && (
                  <p className="text-xs text-amber-700 mb-2 font-medium">
                    ⚠️ Low confidence read — this field may be inaccurate. Retake photo with better
                    lighting or angle for a reliable result.
                  </p>
                )}
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
                {field.type_size_details && (
                  <TypeSizeDetailsView details={field.type_size_details} />
                )}
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
