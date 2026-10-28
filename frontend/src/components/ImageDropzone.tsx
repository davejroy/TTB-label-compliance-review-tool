import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { areaToFileIndex, defaultPanelRole } from "../errorContract";
import { MAX_IMAGES_PER_LABEL, type RetakeArea } from "../types";

interface Props {
  files: File[];
  onChange: (files: File[]) => void;
  idPrefix: string;
}

export interface ImageDropzoneHandle {
  openCapture: (area?: RetakeArea) => void;
}

function ThumbnailImage({ file, url, alt }: { file: File; url: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div
        className="flex h-24 w-full flex-col items-center justify-center rounded-md bg-slate-200 px-2 text-center shadow"
        role="img"
        aria-label={alt}
      >
        <span className="max-w-full truncate text-xs font-semibold text-slate-700">{file.name}</span>
        <span className="mt-1 text-[11px] text-slate-500">Preview not available</span>
      </div>
    );
  }
  return (
    <img
      src={url}
      alt={alt}
      className="h-24 w-full rounded-md object-cover shadow"
      onError={() => setFailed(true)}
    />
  );
}

/**
 * Upload control for a label image(s). Supports three input methods:
 * drag-and-drop, a "Choose File" picker, and a "Take Photo" button.
 *
 * Original files are kept as selected. The backend applies EXIF transpose,
 * quality screening, and model downscale; type-size uses the full-resolution
 * original. Browsers that cannot decode HEIC/HEIF show a filename placeholder.
 */
const ImageDropzone = forwardRef<ImageDropzoneHandle, Props>(function ImageDropzone(
  { files, onChange, idPrefix },
  ref,
) {
  const inputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const replaceIndexRef = useRef<number | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [roles, setRoles] = useState<RetakeArea[]>([]);
  const displayRoles = files.map((_, index) => roles[index] ?? defaultPanelRole(index));

  function clearReplaceIndex() {
    replaceIndexRef.current = null;
  }

  useImperativeHandle(ref, () => ({
    openCapture(area?: RetakeArea) {
      replaceIndexRef.current =
        files.length === 0 ? null : areaToFileIndex(area, files.length, displayRoles);
      cameraInputRef.current?.click();
    },
  }));

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

  useEffect(() => {
    const inputs = [inputRef.current, cameraInputRef.current];
    const onPickerCancel = () => {
      replaceIndexRef.current = null;
    };
    inputs.forEach((el) => el?.addEventListener("cancel", onPickerCancel));
    return () => {
      inputs.forEach((el) => el?.removeEventListener("cancel", onPickerCancel));
    };
  }, []);

  function addFiles(newFiles: FileList | null, options?: { replace?: boolean }) {
    if (!newFiles || newFiles.length === 0) {
      clearReplaceIndex();
      return;
    }
    const incoming = Array.from(newFiles);
    const replaceAt = options?.replace ? replaceIndexRef.current : null;
    clearReplaceIndex();
    if (replaceAt !== null && files.length > 0) {
      const next = [...files];
      next[replaceAt] = incoming[0] ?? next[replaceAt];
      onChange(next);
      return;
    }
    const combined = [...files, ...incoming].slice(0, MAX_IMAGES_PER_LABEL);
    setRoles(combined.map((_, index) => displayRoles[index] ?? defaultPanelRole(index)));
    onChange(combined);
  }

  function removeFile(index: number) {
    clearReplaceIndex();
    setRoles(displayRoles.filter((_, i) => i !== index));
    onChange(files.filter((_, i) => i !== index));
  }

  function replaceFile(index: number) {
    replaceIndexRef.current = index;
    cameraInputRef.current?.click();
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
          clearReplaceIndex();
          addFiles(e.dataTransfer.files);
        }}
      >
        {files.length > 0 && (
          <div className="mb-4 grid grid-cols-2 sm:grid-cols-4 gap-3 w-full">
            {files.map((file, index) => {
              const role = displayRoles[index];
              return (
                <div key={index} className="relative">
                  <ThumbnailImage
                    key={`${file.name}-${file.size}-${file.lastModified}`}
                    file={file}
                    url={objectUrls[index]}
                    alt={`${role} label image`}
                  />
                  <p className="mt-1 text-xs font-semibold uppercase tracking-wide text-slate-600">
                    {role}
                  </p>
                  <button
                    type="button"
                    aria-label={`Replace ${role} photo`}
                    className="mt-1 text-xs font-semibold text-[#15396a] underline"
                    onClick={() => replaceFile(index)}
                  >
                    Replace
                  </button>
                  <button
                    type="button"
                    aria-label="Remove image"
                    className="absolute -top-2 -right-2 flex h-6 w-6 items-center justify-center rounded-full bg-red-600 text-sm font-bold text-white shadow hover:bg-red-700"
                    onClick={() => removeFile(index)}
                  >
                    &times;
                  </button>
                </div>
              );
            })}
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
                onClick={() => {
                  clearReplaceIndex();
                  inputRef.current?.click();
                }}
              >
                Choose File
              </button>
              <button
                type="button"
                className="rounded-lg border-2 border-[#15396a] px-5 py-2.5 text-base font-semibold text-[#15396a] hover:bg-blue-50"
                onClick={() => {
                  clearReplaceIndex();
                  cameraInputRef.current?.click();
                }}
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
          accept="image/png,image/jpeg,image/webp,image/heic,image/heif,.heic,.heif"
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
            const list = e.target.files;
            if (!list || list.length === 0) {
              clearReplaceIndex();
            } else {
              addFiles(list, { replace: replaceIndexRef.current !== null });
            }
            e.target.value = "";
          }}
        />
      </div>
    </div>
  );
});

ImageDropzone.displayName = "ImageDropzone";

export default ImageDropzone;
