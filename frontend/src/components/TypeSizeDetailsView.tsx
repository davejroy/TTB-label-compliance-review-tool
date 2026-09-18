import type { TypeSizeMeasurement } from "../types";

const METHOD_LABELS: Record<string, string> = {
  scale_marker_card_id1: "ISO/IEC 7810 ID-1 Card (85.60 mm)",
  scale_marker_aruco: "Calibrated ArUco Target",
  scale_marker_ruler: "Millimeter Scale Ruler",
  container_geometry: "Known Container Geometry",
  uncalibrated_estimate: "Uncalibrated Photo",
  none: "None",
};

export default function TypeSizeDetailsView({
  details,
}: {
  details: TypeSizeMeasurement;
}) {
  const methodLabel = METHOD_LABELS[details.method] ?? details.method;

  const stateBadge = {
    pass: {
      bg: "bg-emerald-50 border-emerald-300 text-emerald-800",
      dot: "bg-emerald-500",
      label: "Pass (Calibrated mm)",
    },
    fail: {
      bg: "bg-red-50 border-red-300 text-red-800",
      dot: "bg-red-500",
      label: "Deficient Type Size",
    },
    warning: {
      bg: "bg-amber-50 border-amber-300 text-amber-800",
      dot: "bg-amber-500",
      label: "Needs Review / Advisory",
    },
    cannot_measure: {
      bg: "bg-slate-100 border-slate-300 text-slate-800",
      dot: "bg-slate-500",
      label: "Cannot Measure (Retake Advised)",
    },
  }[details.state] ?? {
    bg: "bg-slate-50 border-slate-200 text-slate-700",
    dot: "bg-slate-400",
    label: details.state,
  };

  return (
    <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50/80 p-3.5 space-y-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-base" aria-hidden="true">
            📏
          </span>
          <span className="text-xs font-bold uppercase tracking-wider text-slate-700">
            27 CFR 16.22 Type Size Calibration: {methodLabel}
          </span>
        </div>
        <span
          className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold border ${stateBadge.bg}`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${stateBadge.dot}`} />
          {stateBadge.label}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
        <div className="bg-white p-2 rounded border border-slate-200">
          <span className="text-slate-400 block font-medium">Statutory Min</span>
          <span className="font-bold text-slate-800">
            ≥ {details.required_min_height_mm.toFixed(1)} mm
          </span>
        </div>
        <div className="bg-white p-2 rounded border border-slate-200">
          <span className="text-slate-400 block font-medium">Measured Height</span>
          <span className="font-bold text-slate-800">
            {details.measured_capital_height_mm !== undefined &&
            details.measured_capital_height_mm !== null
              ? `${details.measured_capital_height_mm.toFixed(2)} mm`
              : "N/A (uncalibrated)"}
          </span>
        </div>
        <div className="bg-white p-2 rounded border border-slate-200">
          <span className="text-slate-400 block font-medium">Resolution</span>
          <span className="font-bold text-slate-800">
            {details.pixels_per_mm !== undefined && details.pixels_per_mm !== null
              ? `${details.pixels_per_mm.toFixed(1)} px/mm`
              : "N/A"}
          </span>
        </div>
        <div className="bg-white p-2 rounded border border-slate-200">
          <span className="text-slate-400 block font-medium">Uncertainty / Skew</span>
          <span className="font-bold text-slate-800">
            {details.uncertainty_mm !== undefined && details.uncertainty_mm !== null
              ? `± ${details.uncertainty_mm.toFixed(2)} mm`
              : details.skew_angle_deg !== undefined && details.skew_angle_deg !== null
                ? `${details.skew_angle_deg.toFixed(1)}° skew`
                : "N/A"}
          </span>
        </div>
      </div>

      <p className="text-xs text-slate-600 leading-relaxed bg-white/70 p-2 rounded border border-slate-200/80">
        <span className="font-semibold text-slate-700">Audit Note: </span>
        {details.verification_note}
      </p>
    </div>
  );
}
