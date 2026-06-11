import { useState } from "react";
import { reviewLabel } from "../api";
import { EMPTY_APPLICATION, type ApplicationData, type ReviewResult } from "../types";
import ApplicationForm from "./ApplicationForm";
import ImageDropzone from "./ImageDropzone";
import ResultsPanel from "./ResultsPanel";

export default function SingleReview() {
  const [application, setApplication] = useState<ApplicationData>(EMPTY_APPLICATION);
  const [files, setFiles] = useState<File[]>([]);
  const [result, setResult] = useState<ReviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit =
    files.length > 0 &&
    application.brand_name.trim() !== "" &&
    application.class_type.trim() !== "" &&
    application.alcohol_content.trim() !== "" &&
    application.net_contents.trim() !== "" &&
    !loading;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (files.length === 0) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await reviewLabel(files, application);
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
      <form onSubmit={handleSubmit} className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
        <h2 className="text-2xl font-bold text-slate-900 mb-4">Application Details</h2>
        <ApplicationForm value={application} onChange={setApplication} idPrefix="single" />

        <div className="mt-6">
          <ImageDropzone files={files} onChange={setFiles} idPrefix="single" />
        </div>

        <button
          type="submit"
          disabled={!canSubmit}
          className="mt-6 w-full rounded-lg bg-blue-600 px-6 py-4 text-lg font-bold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {loading ? "Reviewing label..." : "Review Label"}
        </button>

        {error && <p className="mt-3 text-base text-red-700">{error}</p>}
      </form>

      <div>
        <h2 className="text-2xl font-bold text-slate-900 mb-4">Review Results</h2>
        {loading && (
          <div className="rounded-xl border border-slate-200 bg-white p-8 text-center text-slate-500 shadow-sm">
            Reading label and checking against application...
          </div>
        )}
        {!loading && !result && (
          <div className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center text-slate-400">
            Fill out the application details, upload a label image, and click
            "Review Label" to see results here.
          </div>
        )}
        {result && <ResultsPanel result={result} files={files} />}
      </div>
    </div>
  );
}
