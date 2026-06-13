mport { useEffect, useMemo, useState } from "react";import { useEffect, useMemo, useState } from "react";
import type { Confidence, FieldLocation } from "../types";

const CONFIDENCE_COLORS: Record<Confidence, string> = {
      high: "border-green-500 bg-green-400/20",
      medium: "border-amber-500 bg-amber-400/20",
      low: "border-red-500 bg-red-400/20",
};

interface Props {
      /** Uploaded label image files, rendered via object URLs. */
      files: File[];
      /** All field locations reported by Claude for this label (across all images). */
      fieldLocations: FieldLocation[];
      /**
         * The field name currently hovered/selected in the results list, or
            * null if none. Only locations matching this field are outlined.
               */
      highlightedField: string | null;
}

/**
 * Zoomable viewer for a label's uploaded image(s) with bounding-box
  * overlays for the field currently highlighted in the results panel.
   *
    * Object URLs for the thumbnails are created once per files change via
     * useMemo and revoked with a useEffect cleanup to prevent memory leaks.
      *
       * Renders nothing if no images were uploaded (e.g. a CSV-only batch entry).
        */
export default function LabelImageViewer({ files, fieldLocations, highlightedField }: Props) {
      const [zoom, setZoom] = useState(1);

      // Create object URLs once per files array reference change; revoke old ones
      // when files changes or the component unmounts to prevent memory leaks.
      const urls = useMemo(() => files.map((file) => URL.createObjectURL(file)), [files]);
      useEffect(() => {
              return () => {
                        urls.forEach((url) => URL.revokeObjectURL(url));
              };
      }, [urls]);

      if (files.length === 0) return null;

      const highlighted = highlightedField
        ? fieldLocations.filter((loc) => loc.field === highlightedField)
              : [];

      return (
              <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-4">
                    <div className="flex items-center justify-between mb-3">
                            <h4 className="text-base font-semibold text-slate-700">
                                      Label Image{files.length > 1 ? "s" : ""}
                            </h4>h4>
                            <div className="flex items-center gap-2">
                                      <button
                                                      type="button"
                                                      aria-label="Zoom out"
                                                      className="flex h-8 w-8 items-center justify-center rounded-md border border-slate-300 text-lg font-bold text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                                                      onClick={() => setZoom((z) => Math.max(1, Math.round((z - 0.25) * 100) / 100))}
                                                      disabled={zoom <= 1}
                                                    >
                                                  -
                                      </button>button>
                                      <span className="w-12 text-center text-sm text-slate-500">{Math.round(zoom * 100)}%</span>span>
                                      <button
                                                      type="button"
                                                      aria-label="Zoom in"
                                                      className="flex h-8 w-8 items-center justify-center rounded-md border border-slate-300 text-lg font-bold text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                                                      onClick={() => setZoom((z) => Math.min(3, Math.round((z + 0.25) * 100) / 100))}
                                                      disabled={zoom >= 3}
                                                    >
                                                  +
                                      </button>button>
                            </div>div>
                    </div>div>
              
                  {highlightedField && highlighted.length === 0 && (
                          <p className="mb-3 text-sm text-slate-400">
                                    No highlighted region available for this field - the model did not report a location.
                          </p>p>
                    )}
              
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        {files.map((_file, index) => {
                            const boxes = highlighted.filter((loc) => loc.image_index === index);
                            return (
                                            <div
                                                              key={index}
                                                              className="overflow-auto rounded-lg border border-slate-200 bg-slate-50 max-h-[28rem]"
                                                            >
                                                          <div
                                                                              className="relative inline-block"
                                                                              style={{ transform: `scale(${zoom})`, transformOrigin: "top left" }}
                                                                            >
                                                                          <img
                                                                                                src={urls[index]}
                                                                                                alt={`Label image ${index + 1}`}
                                                                                                className="block max-w-full h-auto select-none"
                                                                                              />
                                                              {boxes.map((box, bi) => (
                                                                                                  <div
                                                                                                                          key={bi}
                                                                                                                          className={`absolute border-2 pointer-events-none ${CONFIDENCE_COLORS[box.confidence]}`}
                                                                                                                          style={{
                                                                                                                                                    left: `${box.x * 100}%`,
                                                                                                                                                    top: `${box.y * 100}%`,
                                                                                                                                                    width: `${box.width * 100}%`,
                                                                                                                                                    height: `${box.height * 100}%`,
                                                                                                                              }}
                                                                                                                        />
                                                                                                ))}
                                                          </div>div>
                                            </div>div>
                                          );
              })}
                    </div>div>
                    <p className="mt-2 text-xs text-slate-400">
                            Highlighted regions and confidence levels are AI-generated approximations - use them as a
                            starting point and verify visually.
                    </p>p>
              </div>div>
            );
}</div>
import type { Confidence, FieldLocation } from "../types";

const CONFIDENCE_COLORS: Record<Confidence, string> = {
    high: "border-green-500 bg-green-400/20",
    medium: "border-amber-500 bg-amber-400/20",
    low: "border-red-500 bg-red-400/20",
};

interface Props {
    /** Uploaded label image files, rendered via object URLs. */
  files: File[];
    /** All field locations reported by Claude for this label (across all images). */
  fieldLocations: FieldLocation[];
    /**
     * The `field` name currently hovered/selected in the results list, or
     * `null` if none. Only locations matching this field are outlined.
     */
  highlightedField: string | null;
}

/**
 * Zoomable viewer for a label's uploaded image(s) with bounding-box
 * overlays for the field currently highlighted in the results panel.
 *
 * Object URLs for the thumbnails are created once per `files` change via
 * useMemo and revoked with a useEffect cleanup to prevent memory leaks.
 *
 * Renders nothing if no images were uploaded (e.g. a CSV-only batch entry).
 */
export default function LabelImageViewer({ files, fieldLocations, highlightedField }: Props) {
    const [zoom, setZoom] = useState(1);

  // Create object URLs once per files array reference change; revoke old ones
  // when files changes or the component unmounts to prevent memory leaks.
  const urls = useMemo(() => files.map((file) => URL.createObjectURL(file)), [files]);
    useEffect(() => {
          return () => {
                  urls.forEach((url) => URL.revokeObjectURL(url));
          };
    }, [urls]);

  if (files.length === 0) return null;

  const highlighted = highlightedField
      ? fieldLocations.filter((loc) => loc.field === highlightedField)
        : [];

  return (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-4">
              <div className="flex items-center justify-between mb-3">
                      <h4 className="text-base font-semibold text-slate-700">
                                Label Image{files.length > 1 ? "s" : ""}
                      </h4>h4>
                      <div className="flex items-center gap-2">
                                <button
                                              type="button"
                                              aria-label="Zoom out"
                                              className="flex h-8 w-8 items-center justify-center rounded-md border border-slate-300 text-lg font-bold text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                                              onClick={() => setZoom((z) => Math.max(1, Math.round((z - 0.25) * 100) / 100))}
                                              disabled={zoom <= 1}
                                            >
                                            −
                                </button>button>
                                <span className="w-12 text-center text-sm text-slate-500">{Math.round(zoom * 100)}%</span>span>
                                <button
                                              type="button"
                                              aria-label="Zoom in"
                                              className="flex h-8 w-8 items-center justify-center rounded-md border border-slate-300 text-lg font-bold text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                                              onClick={() => setZoom((z) => Math.min(3, Math.round((z + 0.25) * 100) / 100))}
                                              disabled={zoom >= 3}
                                            >
                                            +
                                </button>button>
                      </div>div>
              </div>div>
        
          {highlightedField && highlighted.length === 0 && (
                  <p className="mb-3 text-sm text-slate-400">
                            No highlighted region available for this field - the model didn't report a location.
                  </p>p>
              )}
        
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {files.map((_file, index) => {
                    const boxes = highlighted.filter((loc) => loc.image_index === index);
                    return (
                                  <div
                                                  key={index}
                                                  className="overflow-auto rounded-lg border border-slate-200 bg-slate-50 max-h-[28rem]"
                                                >
                                                <div
                                                                  className="relative inline-block"
                                                                  style={{ transform: `scale(${zoom})`, transformOrigin: "top left" }}
                                                                >
                                                                <img
                                                                                    src={urls[index]}
                                                                                    alt={`Label image ${index + 1}`}
                                                                                    className="block max-w-full h-auto select-none"
                                                                                  />
                                                  {boxes.map((box, bi) => (
                                                                                    <div
                                                                                                          key={bi}
                                                                                                          className={`absolute border-2 pointer-events-none ${CONFIDENCE_COLORS[box.confidence]}`}
                                                                                                          style={{
                                                                                                                                  left: `${box.x * 100}%`,
                                                                                                                                  top: `${box.y * 100}%`,
                                                                                                                                  width: `${box.width * 100}%`,
                                                                                                                                  height: `${box.height * 100}%`,
                                                                                                            }}
                                                                                                        />
                                                                                  ))}
                                                </div>div>
                                  </div>div>
                                );
        })}
              </div>div>
              <p className="mt-2 text-xs text-slate-400">
                      Highlighted regions and confidence levels are AI-generated approximations - use them as a
                      starting point and verify visually.
              </p>p>
        </div>div>
      );
}</div>
