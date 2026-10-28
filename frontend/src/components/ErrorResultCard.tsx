import type { ErrorCode } from "../types";

export default function ErrorResultCard({
  message,
  errorCode,
  requestId,
}: {
  message: string;
  errorCode?: ErrorCode | null;
  requestId?: string | null;
}) {
  return (
    <div className="rounded-xl border border-red-300 bg-red-50 p-6 space-y-3" data-testid="error-result-card">
      <h3 className="text-xl font-bold text-red-800">Could not process label</h3>
      <p className="text-red-700">{message}</p>
      {errorCode && (
        <p className="text-xs font-mono text-red-800/80" data-testid="error-code">
          {errorCode}
        </p>
      )}
      {requestId && (
        <p className="text-xs text-red-800/70" data-testid="request-id">
          Request ID: {requestId}
        </p>
      )}
    </div>
  );
}
