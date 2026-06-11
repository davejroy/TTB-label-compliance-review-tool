import type { Status } from "../types";

const STYLES: Record<Status, string> = {
  pass: "bg-green-100 text-green-800 border-green-300",
  warning: "bg-amber-100 text-amber-800 border-amber-300",
  fail: "bg-red-100 text-red-800 border-red-300",
};

const LABELS: Record<Status, string> = {
  pass: "Pass",
  warning: "Needs Review",
  fail: "Fail",
};

const ICONS: Record<Status, string> = {
  pass: "✓",
  warning: "⚠",
  fail: "✕",
};

export default function StatusBadge({ status, size = "md" }: { status: Status; size?: "sm" | "md" | "lg" }) {
  const sizeClasses =
    size === "lg"
      ? "text-lg px-4 py-2"
      : size === "sm"
      ? "text-sm px-2 py-1"
      : "text-base px-3 py-1.5";

  return (
    <span
      className={`inline-flex items-center gap-2 font-semibold rounded-full border ${STYLES[status]} ${sizeClasses}`}
    >
      <span aria-hidden="true">{ICONS[status]}</span>
      {LABELS[status]}
    </span>
  );
}
