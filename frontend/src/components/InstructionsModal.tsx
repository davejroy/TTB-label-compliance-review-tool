import { useEffect } from "react";

interface InstructionsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function InstructionsModal({ isOpen, onClose }: InstructionsModalProps) {
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

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 sm:p-6 backdrop-blur-xs overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-labelledby="instructions-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="relative w-full max-w-3xl rounded-2xl bg-white shadow-2xl border border-slate-200 overflow-hidden my-auto max-h-[90vh] flex flex-col">
        {/* Header with TTB navy & gold accent */}
        <div className="bg-[#083c6f] border-b-4 border-[#ffbe2e] px-6 py-4 flex items-center justify-between text-white shrink-0">
          <div className="flex items-center gap-3">
            <span className="text-2xl" aria-hidden="true">📖</span>
            <div>
              <h2 id="instructions-title" className="text-xl font-bold text-white">
                TTB Label Compliance Review Tool — User Guide
              </h2>
              <p className="text-xs sm:text-sm text-slate-200">
                Compliance workflow, photo guidelines, and review interpretation
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-slate-200 hover:text-white hover:bg-white/10 transition-colors focus:outline-none focus:ring-2 focus:ring-[#ffbe2e]"
            aria-label="Close instructions guide"
          >
            <span className="text-2xl font-bold leading-none">&times;</span>
          </button>
        </div>

        {/* Scrollable Content Body */}
        <div className="p-6 sm:p-8 space-y-6 overflow-y-auto text-slate-800 text-sm sm:text-base leading-relaxed">
          {/* Section 1: What the tool does */}
          <section className="space-y-2">
            <h3 className="text-lg font-bold text-[#083c6f] flex items-center gap-2 border-b border-slate-200 pb-1">
              <span>🎯</span> What the Tool Does
            </h3>
            <p className="text-slate-700">
              The TTB Label Compliance Review Tool assists agents, compliance reviewers, and evaluators in auditing beverage alcohol labels against federal labeling standards (27 CFR Parts 4, 5, 7, and 16).
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-2">
              <div className="p-3 bg-blue-50/70 border border-blue-200 rounded-lg">
                <h4 className="font-semibold text-[#15396a] text-sm">1. COLA Application Match (Single &amp; Batch)</h4>
                <p className="text-xs sm:text-sm text-slate-600 mt-1">
                  Compares label images against submitted COLA application fields (Brand Name, Class/Type, ABV, Net Contents). Flags discrepancies, missing fields, or out-of-tolerance ABV values.
                </p>
              </div>
              <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg">
                <h4 className="font-semibold text-slate-900 text-sm">2. Label-Only Check</h4>
                <p className="text-xs sm:text-sm text-slate-600 mt-1">
                  Evaluates label photos directly against mandatory statutory requirements without COLA application data, verifying mandatory statements, addresses, and Government Warnings.
                </p>
              </div>
            </div>
          </section>

          {/* Section 2: Photo Capture & Uploads */}
          <section className="space-y-2">
            <h3 className="text-lg font-bold text-[#083c6f] flex items-center gap-2 border-b border-slate-200 pb-1">
              <span>📸</span> How to Upload &amp; Photo Roles
            </h3>
            <p className="text-slate-700">
              You can upload 1 to 4 images per label item. For cylindrical containers or multi-panel packaging:
            </p>
            <ul className="list-disc list-inside space-y-1 text-slate-700 ml-2">
              <li>
                <strong>Front + Back Panels:</strong> Upload both the front brand panel and back informational panel.
              </li>
              <li>
                <strong>Multi-Photo Merging:</strong> The review engine merges extracted fields across all submitted photos for each item, selecting the highest-confidence extraction per field.
              </li>
              <li>
                <strong>Direct Camera Capture:</strong> On supported mobile/touch devices, use the &ldquo;Take Photo&rdquo; option to capture labels directly.
              </li>
            </ul>
          </section>

          {/* Section 3: Photo Tips */}
          <section className="space-y-2">
            <h3 className="text-lg font-bold text-[#083c6f] flex items-center gap-2 border-b border-slate-200 pb-1">
              <span>💡</span> Photo Tips for Optimal Accuracy
            </h3>
            <div className="bg-amber-50/70 border border-amber-200 rounded-lg p-4 space-y-2">
              <ul className="list-disc list-inside space-y-1.5 text-xs sm:text-sm text-amber-950">
                <li><strong>Flat &amp; Focused:</strong> Keep the label flat and unrolled; tap your device screen to focus text before capturing.</li>
                <li><strong>Even Lighting &amp; Avoid Glare:</strong> Shoot in bright, diffuse light. Avoid direct flash glare or heavy reflections on glossy bottles and metallic foils.</li>
                <li><strong>Straight-On Angle:</strong> Hold the camera parallel to the label surface rather than at an oblique angle.</li>
                <li><strong>Tight Cropping &amp; Close-Ups:</strong> Fill the frame with the label. For small mandatory text (such as net contents or ABV statements), take an additional close-up shot.</li>
              </ul>
            </div>
          </section>

          {/* Section 4: What Happens on Review & Statuses */}
          <section className="space-y-2">
            <h3 className="text-lg font-bold text-[#083c6f] flex items-center gap-2 border-b border-slate-200 pb-1">
              <span>⚖️</span> Understanding Review Results
            </h3>
            <p className="text-slate-700">
              Each reviewed label receives an overall status and per-field breakdown:
            </p>
            <div className="space-y-2 mt-2">
              <div className="flex items-start gap-3 p-2.5 rounded-lg border border-green-200 bg-green-50/60">
                <span className="inline-flex items-center justify-center font-bold text-xs bg-green-100 text-green-800 border border-green-300 rounded-full px-2.5 py-1">
                  ✓ Pass
                </span>
                <span className="text-xs sm:text-sm text-green-950">
                  All mandatory elements are present, conform to statutory text, and match application details within legal tolerances.
                </span>
              </div>
              <div className="flex items-start gap-3 p-2.5 rounded-lg border border-amber-200 bg-amber-50/60">
                <span className="inline-flex items-center justify-center font-bold text-xs bg-amber-100 text-amber-800 border border-amber-300 rounded-full px-2.5 py-1">
                  ⚠ Needs Review
                </span>
                <span className="text-xs sm:text-sm text-amber-950">
                  Potential discrepancies, low-confidence OCR reads, optional exemptions, or formula-dependent items requiring agent verification.
                </span>
              </div>
              <div className="flex items-start gap-3 p-2.5 rounded-lg border border-red-200 bg-red-50/60">
                <span className="inline-flex items-center justify-center font-bold text-xs bg-red-100 text-red-800 border border-red-300 rounded-full px-2.5 py-1">
                  ✕ Fail
                </span>
                <span className="text-xs sm:text-sm text-red-950">
                  Definite non-compliance (missing mandatory statement, out-of-tolerance ABV, mismatched brand name, or malformed Government Warning).
                </span>
              </div>
            </div>
          </section>

          {/* Section 5: Retake vs Fail */}
          <section className="space-y-2">
            <h3 className="text-lg font-bold text-[#083c6f] flex items-center gap-2 border-b border-slate-200 pb-1">
              <span>🔄</span> Retake Prompts vs. Hard Compliance Fails
            </h3>
            <p className="text-slate-700">
              It is vital to distinguish between optical quality issues and regulatory violations:
            </p>
            <ul className="list-disc list-inside space-y-1.5 text-slate-700 ml-2">
              <li>
                <strong>LowConfidence Retake Prompts:</strong> Triggered when photo blur, shadows, or glare prevent clear OCR transcription. The tool advises retaking the photo to prevent false failures.
              </li>
              <li>
                <strong>Hard Compliance Fails:</strong> Triggered when clear label text directly violates TTB regulations (e.g. incorrect Surgeon General warning phrasing, missing bottler address, or ABV outside allowable 27 CFR tolerances).
              </li>
            </ul>
          </section>

          {/* Section 6: Specific Compliance Rules */}
          <section className="space-y-2">
            <h3 className="text-lg font-bold text-[#083c6f] flex items-center gap-2 border-b border-slate-200 pb-1">
              <span>📋</span> Key Mandatory Rules &amp; Caveats
            </h3>
            <div className="space-y-2 text-slate-700">
              <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg">
                <strong className="text-slate-900 block mb-1">Government Warning Exact Casing (27 CFR 16.20):</strong>
                <p className="text-xs sm:text-sm text-slate-600">
                  The heading must appear in exact uppercase as <code className="font-mono font-bold bg-white px-1.5 py-0.5 rounded border border-slate-300 text-slate-900">GOVERNMENT WARNING:</code> followed by the statutory text. Any altered header casing or text mutation triggers a compliance warning or failure.
                </p>
              </div>
              <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg">
                <strong className="text-slate-900 block mb-1">Formula-Dependent Warnings (Sulfites &amp; Allergens):</strong>
                <p className="text-xs sm:text-sm text-slate-600">
                  Declarations such as sulfites (10+ ppm) or major food allergens depend on the underlying beverage formula and production records. When detected or omitted, they are flagged with formula-dependent advisory notes.
                </p>
              </div>
            </div>
          </section>

          {/* Section 7: Known Gaps */}
          <section className="space-y-2">
            <h3 className="text-lg font-bold text-[#083c6f] flex items-center gap-2 border-b border-slate-200 pb-1">
              <span>⚠️</span> Known Gaps &amp; Current Scope
            </h3>
            <p className="text-xs sm:text-sm text-slate-700 bg-slate-100 p-3 rounded-lg border border-slate-300">
              <strong>Known gaps &amp; Scope:</strong> Modernized standards of fill under T.D. TTB-200 (effective Jan 2025) are fully automated for wine (§4.72) and spirits (§5.203). Exact type-size verification under 27 CFR 16.22 (physical millimeter measurement) is not measured from uncalibrated photos and still requires a physical gauge or scale.
            </p>
          </section>
        </div>

        {/* Footer */}
        <div className="bg-slate-50 border-t border-slate-200 px-6 py-4 flex justify-end shrink-0">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-[#15396a] px-5 py-2.5 text-sm font-bold text-white hover:bg-[#0b1f3a] transition-colors focus:outline-none focus:ring-2 focus:ring-[#ffbe2e]"
          >
            Got it, Close Guide
          </button>
        </div>
      </div>
    </div>
  );
}
