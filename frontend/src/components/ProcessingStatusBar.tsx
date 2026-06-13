interface Props {
  /** Current active step index (0-based). Pass -1 when idle. */
  step: number;
  /** True once processing has fully completed (result arrived or error). */
  done: boolean;
}

const STAGES = [
  { label: "Uploading",   icon: "upload" },
  { label: "Reading label", icon: "eye" },
  { label: "Checking",    icon: "check-list" },
  { label: "Complete",    icon: "complete" },
];

function StageIcon({ name, active, done }: { name: string; active: boolean; done: boolean }) {
  const base = "flex items-center justify-center w-8 h-8 rounded-full border-2 transition-all duration-500";
  const cls = done
    ? base + " bg-green-500 border-green-500 text-white"
    : active
      ? base + " bg-[#15396a] border-[#15396a] text-white animate-pulse"
      : base + " bg-white border-slate-300 text-slate-400";

  if (name === "upload") return (
    <span className={cls}>
      <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 12V4m0 0L8 8m4-4l4 4" />
      </svg>
    </span>
  );
  if (name === "eye") return (
    <span className={cls}>
      <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.477 0 8.268 2.943 9.542 7-1.274 4.057-5.065 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
      </svg>
    </span>
  );
  if (name === "check-list") return (
    <span className={cls}>
      <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
      </svg>
    </span>
  );
  // complete checkmark
  return (
    <span className={cls}>
      <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
      </svg>
    </span>
  );
}

export default function ProcessingStatusBar({ step, done }: Props) {
  const totalStages = STAGES.length; // 4

  return (
    <div className="w-full px-2 py-4">
      <div className="flex items-center">
        {STAGES.map((stage, i) => {
          const isComplete = done ? true : i < step;
          const isActive   = !done && i === step;
          
          return (
            <div key={stage.label} className="flex items-center" style={{ flex: i < totalStages - 1 ? "1" : "0" }}>
              {/* Circle + label */}
              <div className="flex flex-col items-center gap-1 min-w-[64px]">
                <StageIcon name={stage.icon} active={isActive} done={isComplete} />
                <span
                  className={
                    "text-xs font-semibold text-center transition-colors duration-500 " +
                    (isComplete
                      ? "text-green-600"
                      : isActive
                        ? "text-[#15396a]"
                        : "text-slate-400")
                  }
                >
                  {stage.label}
                </span>
              </div>
              {/* Connector line between stages */}
              {i < totalStages - 1 && (
                <div className="flex-1 mx-1 mb-5">
                  <div className="h-0.5 w-full bg-slate-200 relative overflow-hidden rounded">
                    <div
                      className="absolute inset-y-0 left-0 bg-green-500 transition-all duration-700"
                      style={{ width: isComplete ? "100%" : "0%" }}
                    />
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
