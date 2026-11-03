import type { RetakeArea, RetakeGuidance } from "../types";
import { RETAKE_MESSAGE } from "../errorContract";

const CAUSE_LABELS: Record<string, string> = {
  blur: "Blur",
  glare: "Glare",
  blank: "Blank photo",
  dark: "Too dark",
  angle: "Angle",
  curvature: "Curvature",
  cropped: "Cropped",
  low_resolution: "Low resolution",
};

export default function RetakeCard({
  message,
  retake,
  onRetake,
}: {
  message?: string | null;
  retake?: RetakeGuidance | null;
  onRetake?: (area: RetakeArea) => void;
}) {
  const area = retake?.area ?? "front";
  return (
    <div className="rounded-xl border border-amber-300 bg-amber-50 p-6 space-y-4" data-testid="retake-card">
      <h3 className="text-xl font-bold text-amber-900">{message || RETAKE_MESSAGE}</h3>
      {retake && (
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm text-amber-950">
          <div>
            <dt className="font-semibold uppercase tracking-wide text-amber-700 text-xs">Field</dt>
            <dd className="mt-0.5">{retake.field}</dd>
          </div>
          <div>
            <dt className="font-semibold uppercase tracking-wide text-amber-700 text-xs">Area</dt>
            <dd className="mt-0.5 capitalize">{retake.area}</dd>
          </div>
          <div>
            <dt className="font-semibold uppercase tracking-wide text-amber-700 text-xs">Cause</dt>
            <dd className="mt-0.5">{CAUSE_LABELS[retake.cause] ?? retake.cause}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="font-semibold uppercase tracking-wide text-amber-700 text-xs">Tip</dt>
            <dd className="mt-0.5">{retake.tip}</dd>
          </div>
        </dl>
      )}
      <button
        type="button"
        className="rounded-lg bg-[#15396a] px-5 py-2.5 text-base font-semibold text-white hover:bg-[#0b1f3a]"
        onClick={() => onRetake?.(area)}
      >
        Retake {area} photo
      </button>
    </div>
  );
}
