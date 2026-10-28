import { useState } from "react";
import type { FormatStatus, WarningFormatResult, WarningTextDiff } from "../types";
import TypeSizeDetailsView from "./TypeSizeDetailsView";
import type { TypeSizeMeasurement } from "../types";

const FORMAT_STYLES: Record<FormatStatus, { bg: string; label: string }> = {
  pass: {
    bg: "bg-emerald-50 border-emerald-300 text-emerald-800",
    label: "Pass",
  },
  advisory: {
    bg: "bg-amber-50 border-amber-300 text-amber-800",
    label: "Advisory",
  },
  fail: {
    bg: "bg-red-50 border-red-300 text-red-800",
    label: "Fail",
  },
};

function WordDiffView({ diff }: { diff: WarningTextDiff }) {
  if (diff.exact_match) {
    return (
      <p className="text-xs text-slate-600">
        Word-level comparison matches the exact 27 CFR 16.21 statement.
      </p>
    );
  }

  return (
    <div className="space-y-2 text-xs">
      <p className="font-semibold text-slate-700">Word-level differences vs. 27 CFR 16.21</p>
      {diff.missing_words.length > 0 && (
        <ul className="list-disc list-inside text-red-800">
          {diff.missing_words.map((item, idx) => (
            <li key={`missing-${idx}`}>
              Missing &lsquo;{item.expected}&rsquo; at expected position {item.expected_index}
            </li>
          ))}
        </ul>
      )}
      {diff.extra_words.length > 0 && (
        <ul className="list-disc list-inside text-amber-800">
          {diff.extra_words.map((item, idx) => (
            <li key={`extra-${idx}`}>
              Extra &lsquo;{item.observed}&rsquo; at observed position {item.observed_index}
            </li>
          ))}
        </ul>
      )}
      {diff.changed_words.length > 0 && (
        <ul className="list-disc list-inside text-red-800">
          {diff.changed_words.map((item, idx) => (
            <li key={`changed-${idx}`}>
              Changed &lsquo;{item.expected}&rsquo; → &lsquo;{item.observed}&rsquo; (expected pos{" "}
              {item.expected_index}, observed pos {item.observed_index})
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function WarningFormatView({
  format,
  typeSize,
  textDiff,
}: {
  format?: WarningFormatResult;
  typeSize?: TypeSizeMeasurement;
  textDiff?: WarningTextDiff;
}) {
  const [expanded, setExpanded] = useState(false);

  if (!format && !typeSize && !textDiff) {
    return null;
  }

  const status = format?.status ?? "advisory";
  const badge = FORMAT_STYLES[status];

  return (
    <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50/80 p-3.5 space-y-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold uppercase tracking-wider text-slate-700">
            27 CFR 16.22 Warning Format
          </span>
          {format && (
            <span
              className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold border ${badge.bg}`}
            >
              {badge.label}
            </span>
          )}
        </div>
        <button
          type="button"
          className="text-xs font-semibold text-[#15396a] underline"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "Hide details" : "Show details"}
        </button>
      </div>

      {format && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs">
          <div className="bg-white p-2 rounded border border-slate-200">
            <span className="text-slate-400 block font-medium">Min type size</span>
            <span className="font-bold text-slate-800">
              ≥ {format.required_min_height_mm.toFixed(1)} mm
            </span>
          </div>
          <div className="bg-white p-2 rounded border border-slate-200">
            <span className="text-slate-400 block font-medium">Max characters/inch</span>
            <span className="font-bold text-slate-800">{format.max_characters_per_inch}</span>
          </div>
          <div className="bg-white p-2 rounded border border-slate-200">
            <span className="text-slate-400 block font-medium">Estimated CPI</span>
            <span className="font-bold text-slate-800">
              {format.estimated_characters_per_inch != null
                ? format.estimated_characters_per_inch.toFixed(1)
                : "N/A"}
            </span>
          </div>
        </div>
      )}

      {expanded && (
        <div className="space-y-3">
          {format?.findings.map((finding, idx) => (
            <div key={`${finding.code}-${idx}`} className="bg-white p-2 rounded border border-slate-200 text-xs">
              <p className="font-semibold text-slate-700">
                {finding.citation}
                {finding.estimated ? " · estimate" : ""} · {finding.status}
              </p>
              <p className="text-slate-600 mt-1">{finding.message}</p>
            </div>
          ))}
          {textDiff && <WordDiffView diff={textDiff} />}
          {typeSize && <TypeSizeDetailsView details={typeSize} />}
        </div>
      )}
    </div>
  );
}
