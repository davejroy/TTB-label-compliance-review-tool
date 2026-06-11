import { useRef, useState } from "react";

interface Props {
  file: File | null;
  onChange: (file: File | null) => void;
  idPrefix: string;
}

export default function ImageDropzone({ file, onChange, idPrefix }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  const previewUrl = file ? URL.createObjectURL(file) : null;

  function handleFiles(files: FileList | null) {
    if (files && files.length > 0) {
      onChange(files[0]);
    }
  }

  return (
    <div>
      <label className="block text-base font-semibold text-slate-700 mb-1" htmlFor={`${idPrefix}-file`}>
        Label Image
      </label>
      <div
        className={`relative flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-6 text-center transition-colors ${
          dragActive ? "border-blue-500 bg-blue-50" : "border-slate-300 bg-slate-50"
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragActive(false);
          handleFiles(e.dataTransfer.files);
        }}
      >
        {previewUrl ? (
          <div className="flex flex-col items-center gap-3">
            <img src={previewUrl} alt="Label preview" className="max-h-48 rounded-md shadow" />
            <p className="text-sm text-slate-600">{file?.name}</p>
            <button
              type="button"
              className="text-sm font-semibold text-blue-700 underline"
              onClick={() => onChange(null)}
            >
              Remove image
            </button>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2 py-6">
            <span className="text-4xl" aria-hidden="true">
              📷
            </span>
            <p className="text-lg font-medium text-slate-700">
              Drag &amp; drop a label photo here
            </p>
            <p className="text-sm text-slate-500">or</p>
            <button
              type="button"
              className="rounded-lg bg-blue-600 px-5 py-2.5 text-base font-semibold text-white hover:bg-blue-700"
              onClick={() => inputRef.current?.click()}
            >
              Choose File
            </button>
          </div>
        )}
        <input
          ref={inputRef}
          id={`${idPrefix}-file`}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>
    </div>
  );
}
