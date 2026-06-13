import { useEffect, useMemo, useRef, useState } from "react";
import { MAX_IMAGES_PER_LABEL } from "../types";

interface Props {
  files: File[];
  onChange: (files: File[]) => void;
  idPrefix: string;
}

/**
 * Detect phones and tablets so we can show a "Take Photo" button that
 * opens the device camera via input capture.
 *
 * Three-stage detection handles the full browser/OS matrix:
 * 1. navigator.userAgentData.mobile  - Chromium-only but most accurate.
 * 2. Classic UA regex                - Android, iPhone, older iPads.
 * 3. Touch + no fine pointer         - Modern iPads (iPadOS 13+) report a
 *    Mac-like UA, but they have touch points AND lack a fine (mouse) pointer.
 *    Touchscreen laptops always expose a fine pointer too, so they are
 *    correctly excluded.
 *
 * We deliberately avoid (pointer: coarse) / maxTouchPoints alone because
 * touchscreen Windows laptops satisfy both and would show the button on desktop.
 */
function isMobileDevice(): boolean {
  if (typeof navigator === "undefined" || typeof window === "undefined") return false;

  // Stage 1: Chromium User-Agent Client Hints (most reliable when available)
  const uaData = (navigator as Navigator & { userAgentData?: { mobile?: boolean } })
    .userAgentData;
  if (uaData?.mobile !== undefined) return uaData.mobile;

  // Stage 2: Classic UA string - covers Android, iPhone, and pre-iPadOS-13 iPads
  if (/Android|iPhone|iPad|iPod/i.test(navigator.userAgent)) return true;

  // Stage 3: Modern iPads (iPadOS 13+) advertise a Mac UA but have no mouse.
  // A real laptop/desktop always has a fine pointer; an iPad has only coarse.
  const hasTouch = navigator.maxTouchPoints > 1;
  const noFinePointer = !window.matchMedia("(pointer: fine)").matches;
  return hasTouch && noFinePointer;
}

/**
 * Upload control for a label image(s). Supports three input methods:
 * drag-and-drop, a "Choose File" picker (always shown), and on phones
 * and tablets only a "Take Photo" button that opens the device camera.
 *
 * Object URLs for thumbnails are created via useMemo and explicitly revoked
 * when the file list changes, preventing memory leaks on long review sessions.
 */
export default function ImageDropzone({ files, onChange, idPrefix }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  // Evaluated once at mount - the device type cannot change mid-session.
  const [canUseCamera] = useState(isMobileDevice);

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

  function addFiles(newFiles: FileList | null) {
    if (!newFiles || newFiles.length === 0) return;
    const combined = [...files, ...Array.from(newFiles)].slice(0, MAX_IMAGES_PER_LABEL);
    onChange(combined);
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
            {files.map((file, index) => (
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

        {!atLimit && (
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
              {canUseCamera && (
                <button
                  type="button"
                  className="rounded-lg border-2 border-[#15396a] px-5 py-2.5 text-base font-semibold text-[#15396a] hover:bg-blue-50"
                  onClick={() => cameraInputRef.current?.click()}
                >
                  Take Photo
                </button>
              )}
            </div>
          </div>
        )}

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
        {canUseCamera && (
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
        )}
      </div>
    </div>
  );
}
