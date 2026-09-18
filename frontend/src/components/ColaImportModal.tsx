import { useEffect, useRef, useState } from "react";
import { getColaPresets, parseColaTemplate } from "../api";
import { downloadColaTemplateCsv } from "../csv";
import type { ApplicationData, ColaPreset } from "../types";

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSelectApplication: (application: ApplicationData, multiple?: ApplicationData[]) => void;
  isBatchMode?: boolean;
}

export default function ColaImportModal({
  isOpen,
  onClose,
  onSelectApplication,
  isBatchMode = false,
}: Props) {
  const [presets, setPresets] = useState<ColaPreset[]>([]);
  const [loadingPresets, setLoadingPresets] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Close on Escape key press
  useEffect(() => {
    if (!isOpen) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  // Load presets when modal opens
  useEffect(() => {
    if (!isOpen) return;

    let active = true;
    getColaPresets()
      .then((data) => {
        if (active) {
          setPresets(data);
          setLoadingPresets(false);
        }
      })
      .catch((err) => {
        if (active) {
          // Non-fatal: fallback to hardcoded defaults if network blip
          setLoadingPresets(false);
          console.warn("Could not fetch remote presets:", err);
        }
      });

    return () => {
      active = false;
    };
  }, [isOpen]);

  if (!isOpen) return null;

  function handleClose() {
    setError(null);
    onClose();
  }

  async function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    setImporting(true);
    setError(null);

    try {
      const parsedApps = await parseColaTemplate(file);
      if (!parsedApps || parsedApps.length === 0) {
        throw new Error("No application data found in the template file.");
      }

      if (isBatchMode) {
        onSelectApplication(parsedApps[0], parsedApps);
      } else {
        onSelectApplication(parsedApps[0]);
      }
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to import template file.");
    } finally {
      setImporting(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }

  function handlePresetSelect(preset: ColaPreset) {
    onSelectApplication(preset.application);
    handleClose();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 sm:p-6 backdrop-blur-xs overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-labelledby="cola-import-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div className="relative w-full max-w-2xl rounded-2xl bg-white shadow-2xl border border-slate-200 overflow-hidden my-auto max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="bg-[#083c6f] border-b-4 border-[#ffbe2e] px-6 py-4 flex items-center justify-between text-white shrink-0">
          <div className="flex items-center gap-3">
            <span className="text-2xl" aria-hidden="true">📄</span>
            <div>
              <h2 id="cola-import-title" className="text-xl font-bold text-white">
                Pre-fill COLA Application Data
              </h2>
              <p className="text-xs sm:text-sm text-slate-200">
                Import application details via CSV/JSON or select a demo preset
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleClose}
            className="rounded-lg p-2 text-slate-200 hover:text-white hover:bg-white/10 transition-colors focus:outline-none focus:ring-2 focus:ring-[#ffbe2e]"
            aria-label="Close COLA pre-fill modal"
          >
            <span className="text-2xl font-bold leading-none">&times;</span>
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-6 space-y-6 overflow-y-auto">
          {error && (
            <div className="p-4 rounded-lg bg-red-50 border border-red-200 text-sm text-red-800 flex items-start gap-2">
              <span className="text-red-500 font-bold shrink-0">✕</span>
              <div>
                <p className="font-semibold">Import Error</p>
                <p className="mt-0.5">{error}</p>
              </div>
            </div>
          )}

          {/* Section 1: Template Import */}
          <section className="space-y-3">
            <h3 className="text-base font-bold text-[#083c6f] flex items-center justify-between border-b border-slate-200 pb-2">
              <span className="flex items-center gap-2">
                <span>📁</span> Upload CSV or JSON Template
              </span>
              <button
                type="button"
                onClick={downloadColaTemplateCsv}
                className="text-xs font-semibold text-[#15396a] hover:underline flex items-center gap-1"
              >
                <span>⬇️</span> Download Sample CSV
              </button>
            </h3>
            <p className="text-xs sm:text-sm text-slate-600">
              Upload a spreadsheet or JSON export containing standard COLA fields (Brand Name, Class/Type, ABV, Net Contents, Bottler Address).
              {isBatchMode && " In Batch mode, multi-row files will populate multiple label review items."}
            </p>

            <div className="mt-3">
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.json,text/csv,application/json"
                className="hidden"
                id="cola-template-upload"
                onChange={handleFileUpload}
                disabled={importing}
              />
              <label
                htmlFor="cola-template-upload"
                className={`flex flex-col items-center justify-center border-2 border-dashed rounded-xl p-6 cursor-pointer transition-colors ${
                  importing
                    ? "bg-slate-100 border-slate-300 cursor-not-allowed"
                    : "border-[#15396a]/40 bg-blue-50/40 hover:bg-blue-50 hover:border-[#15396a]"
                }`}
              >
                {importing ? (
                  <div className="flex items-center gap-2 text-slate-600 font-medium text-sm">
                    <svg className="animate-spin h-5 w-5 text-[#15396a]" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                    </svg>
                    Validating and parsing template…
                  </div>
                ) : (
                  <>
                    <span className="text-2xl mb-1" aria-hidden="true">📤</span>
                    <span className="text-sm font-bold text-[#15396a]">
                      Click to choose CSV or JSON file
                    </span>
                    <span className="text-xs text-slate-500 mt-1">
                      Supports .csv, .json with standard RFC 4180 headers
                    </span>
                  </>
                )}
              </label>
            </div>
          </section>

          {/* Section 2: Demo Presets */}
          <section className="space-y-3">
            <h3 className="text-base font-bold text-[#083c6f] flex items-center gap-2 border-b border-slate-200 pb-2">
              <span>⚡</span> One-Click Demo Presets
            </h3>
            <p className="text-xs sm:text-sm text-slate-600">
              Select a pre-configured, statutory-compliant COLA application for quick evaluation testing:
            </p>

            {loadingPresets ? (
              <div className="p-4 text-center text-slate-500 text-sm">Loading presets…</div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {presets.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    onClick={() => handlePresetSelect(preset)}
                    className="text-left p-3.5 rounded-xl border border-slate-200 hover:border-[#15396a] hover:bg-blue-50/50 transition-all shadow-xs group"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-bold uppercase tracking-wider text-slate-500 group-hover:text-[#15396a]">
                        {preset.application.beverage_type.replace("_", " ")}
                      </span>
                      <span className="text-xs font-semibold text-[#15396a] group-hover:underline">
                        Apply →
                      </span>
                    </div>
                    <div className="font-bold text-slate-900 text-sm mt-1">
                      {preset.application.brand_name}
                    </div>
                    <div className="text-xs text-slate-600 mt-0.5">
                      {preset.application.class_type} • {preset.application.alcohol_content} • {preset.application.net_contents}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </section>
        </div>

        {/* Footer */}
        <div className="bg-slate-50 border-t border-slate-200 px-6 py-3 flex justify-end shrink-0">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
