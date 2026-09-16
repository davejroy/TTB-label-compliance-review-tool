import { useEffect, useMemo, useRef, useState } from "react";
import { MAX_IMAGES_PER_LABEL } from "../types";
import { ensureImagesResized } from "../imageUtils";

interface Props {
  files: File[];
  onChange: (files: File[]) => void;
  idPrefix: string;
}

/**
 * Upload control for a label image(s). Supports three input methods:
 * drag-and-drop, a "Choose File" picker, and a "Take Photo" button.
 *
 * Images are automatically downscaled client-side (max 1600 px on the long edge,
 * matching backend intent) before being added to state. This cuts upload time
 * dramatically without any visible quality loss in OCR results.
 */
export default function ImageDropzone({ files, onChange, idPrefix }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  const [resizing, setResizing] = useState(false);

  // Create object URLs once per files array change; revoke old ones to avoid
  // memory leaks from long review sessions with many image swaps.
  const objectUrls = useMemo(
    () => files.map((f) => URL.createObjectURL(f)),
    [files],
  );
  useEffect(() => {
    return () => {
      objectUrls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [objectUrls]);

  async function addFiles(newFiles: FileList | null) {
    if (!newFiles || newFiles.length === 0) return;
    setResizing(true);
    try {
      const resized = await ensureImagesResized(Array.from(newFiles));
      const combined = [...files, ...resized].slice(0, MAX_IMAGES_PER_LABEL);
      onChange(combined);
    } finally {
      setResizing(false);
    }
  }

  function removeFile(index: number) {
    onChange(files.filter((_, i) => i !== index));
  }

  const atLimit = files.length >= MAX_IMAGES_PER_LABEL;

  return (
    <div>
      <label className="block text-base font-semibold text-slate-700 mb-1" htmlFor={`${idPrefix}-file`}>
        Label Images{" "}
        <span className="font-normal text-slate-500">
          (1-{MAX_IMAGES_PER_LABEL}, e.g. front &amp; back)
        </span>
      </label>
      <div
        className={`relative flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-6 text-center transition-colors ${
          dragActive ? "border-[#15396a] bg-blue-50" : "border-slate-300 bg-slate-50"
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          if (!atLimit) setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragActive(false);
          addFiles(e.dataTransfer.files);
        }}
      >
        {files.length > 0 && (
          <div className="mb-4 grid grid-cols-2 sm:grid-cols-4 gap-3 w-full">
            {files.map((_file, index) => (
              <div key={index} className="relative">
                <img
                  src={objectUrls[index]}
                  alt={`Label image ${index + 1}`}
                  className="h-24 w-full rounded-md object-cover shadow"
                />
                <button
                  type="button"
                  aria-label="Remove image"
                  className="absolute -top-2 -right-2 flex h-6 w-6 items-center justify-center rounded-full bg-red-600 text-sm font-bold text-white shadow hover:bg-red-700"
                  onClick={() => removeFile(index)}
                >
                  &times;
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Resizing spinner — shown while canvas downscale is in progress */}
        {resizing && (
          <div className="flex items-center gap-2 py-2 text-sm text-slate-500">
            <svg className="animate-spin h-4 w-4 text-[#15396a]" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
            </svg>
            Optimising image…
          </div>
        )}

        {!atLimit && !resizing && (
          <div className="flex flex-col items-center gap-2 py-4">
            <span className="text-4xl" aria-hidden="true">
              📷
            </span>
            <p className="text-lg font-medium text-slate-700">
              {files.length === 0
                ? "Drag & drop label photos here"
                : `Add another image (${files.length}/${MAX_IMAGES_PER_LABEL})`}
            </p>
            <p className="text-sm text-slate-500">or</p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <button
                type="button"
                className="rounded-lg bg-[#15396a] px-5 py-2.5 text-base font-semibold text-white hover:bg-[#0b1f3a]"
                onClick={() => inputRef.current?.click()}
              >
                Choose File
              </button>
              <button
                type="button"
                className="rounded-lg border-2 border-[#15396a] px-5 py-2.5 text-base font-semibold text-[#15396a] hover:bg-blue-50"
                onClick={() => cameraInputRef.current?.click()}
              >
                Take Photo
              </button>
            </div>
          </div>
        )}

        {/* Standard multi-file picker — no capture, accepts common image formats */}
        <input
          ref={inputRef}
          id={`${idPrefix}-file`}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          multiple
          className="hidden"
          onChange={(e) => {
            addFiles(e.target.files);
            e.target.value = "";
          }}
        />
        {/* Camera input — capture="environment" opens rear camera on mobile;
            desktop browsers ignore the attribute and show a normal file picker */}
        <input
          ref={cameraInputRef}
          id={`${idPrefix}-camera`}
          type="file"
          accept="image/*"
          capture="environment"
          className="hidden"
          onChange={(e) => {
            addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
    </div>
  );
}
