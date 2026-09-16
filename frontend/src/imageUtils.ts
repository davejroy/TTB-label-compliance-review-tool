/**
 * Client-side image downscaling utility for the TTB Label Compliance Review Tool.
 *
 * Phone cameras and modern scanners produce 12–48 MP images (4000–8000 px).
 * Uploading uncompressed images wastes client bandwidth, increases network latency,
 * and exceeds the resolution needed for OCR and vision models.
 *
 * This module ensures images are resized client-side to fit within the backend's
 * target dimension (MAX_IMAGE_DIMENSION = 1600 px on the long edge) before upload.
 *
 * Quality & legibility preservation:
 * - Aspect ratio is strictly preserved.
 * - Canvas smoothing is enabled with "high" interpolation quality.
 * - White background is pre-filled on canvas before drawing so transparent PNGs
 *   do not produce black backgrounds when converted to JPEG.
 * - JPEG quality is set to 0.92 to maintain sharp edges on fine print (warning label,
 *   net contents, ABV).
 * - Images already within 1600px are preserved without re-encoding to avoid generation loss.
 * - If image loading or canvas operations fail (e.g. unsupported format or non-browser env),
 *   the original file is returned fail-open so the backend validation can inspect it.
 */

export const MAX_IMAGE_DIMENSION = 1600;
export const RESIZE_QUALITY = 0.92;

/**
 * Calculate scaled dimensions fitting within maxDim while preserving aspect ratio.
 */
export function calculateTargetDimensions(
  width: number,
  height: number,
  maxDim: number = MAX_IMAGE_DIMENSION,
): { width: number; height: number; scaled: boolean } {
  if (width <= 0 || height <= 0) {
    return { width, height, scaled: false };
  }

  const maxSide = Math.max(width, height);
  if (maxSide <= maxDim) {
    return { width, height, scaled: false };
  }

  const scale = maxDim / maxSide;
  const targetWidth = Math.max(1, Math.round(width * scale));
  const targetHeight = Math.max(1, Math.round(height * scale));

  return { width: targetWidth, height: targetHeight, scaled: true };
}

/**
 * Resize a single image File client-side using an off-screen HTML canvas.
 *
 * @param file  The input File object (JPEG, PNG, WebP, etc.)
 * @param maxDim  Maximum long-edge dimension in pixels (default 1600)
 * @param quality  JPEG export quality 0-1 (default 0.92)
 * @returns  Promise resolving to a resized File (image/jpeg) or the original File if within limits/unsupported.
 */
export async function resizeImageFile(
  file: File,
  maxDim: number = MAX_IMAGE_DIMENSION,
  quality: number = RESIZE_QUALITY,
): Promise<File> {
  // Non-image files or environments without window/document support pass through fail-open
  if (typeof window === "undefined" || typeof document === "undefined" || !file.type.startsWith("image/")) {
    return file;
  }

  // Quick capability check for canvas support
  try {
    const testCanvas = document.createElement("canvas");
    if (typeof testCanvas.getContext !== "function") {
      return file;
    }
  } catch {
    return file;
  }

  // 1. Try createImageBitmap if available in modern browsers
  if (typeof createImageBitmap === "function") {
    try {
      const bitmap = await createImageBitmap(file);
      const { width: w, height: h } = bitmap;
      const { width: dw, height: dh, scaled } = calculateTargetDimensions(w, h, maxDim);

      if (!scaled) {
        if (typeof bitmap.close === "function") bitmap.close();
        return file;
      }

      const canvas = document.createElement("canvas");
      canvas.width = dw;
      canvas.height = dh;
      const ctx = canvas.getContext("2d");

      if (!ctx || typeof canvas.toBlob !== "function") {
        if (typeof bitmap.close === "function") bitmap.close();
        return file;
      }

      ctx.fillStyle = "#FFFFFF";
      ctx.fillRect(0, 0, dw, dh);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(bitmap, 0, 0, dw, dh);
      if (typeof bitmap.close === "function") bitmap.close();

      return await new Promise<File>((resolve) => {
        canvas.toBlob(
          (blob) => {
            if (!blob) {
              resolve(file);
              return;
            }
            const baseName = file.name.replace(/\.[^.]+$/, "");
            const newName = `${baseName}_resized.jpg`;
            resolve(
              new File([blob], newName, {
                type: "image/jpeg",
                lastModified: Date.now(),
              }),
            );
          },
          "image/jpeg",
          quality,
        );
      });
    } catch {
      // Fall through to Image-based fallback
    }
  }

  // 2. Fallback using Image element with safety timeout
  return new Promise<File>((resolve) => {
    let objectUrl = "";
    try {
      objectUrl = URL.createObjectURL(file);
    } catch {
      resolve(file);
      return;
    }

    const img = new Image();

    // Safety timeout: if Image.onload never fires (e.g. happy-dom/test env without image decoding),
    // resolve within 500ms so tests and offline environments never hang.
    const safetyTimer = setTimeout(() => {
      cleanup();
      resolve(file);
    }, 500);

    const cleanup = () => {
      clearTimeout(safetyTimer);
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = "";
      }
    };

    img.onload = () => {
      const { naturalWidth: w, naturalHeight: h } = img;

      const { width: dw, height: dh, scaled } = calculateTargetDimensions(w, h, maxDim);

      if (!scaled) {
        cleanup();
        resolve(file);
        return;
      }

      try {
        const canvas = document.createElement("canvas");
        canvas.width = dw;
        canvas.height = dh;
        const ctx = canvas.getContext("2d");

        if (!ctx || typeof canvas.toBlob !== "function") {
          cleanup();
          resolve(file);
          return;
        }

        ctx.fillStyle = "#FFFFFF";
        ctx.fillRect(0, 0, dw, dh);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(img, 0, 0, dw, dh);

        canvas.toBlob(
          (blob) => {
            cleanup();
            if (!blob) {
              resolve(file);
              return;
            }

            const baseName = file.name.replace(/\.[^.]+$/, "");
            const newName = `${baseName}_resized.jpg`;
            resolve(
              new File([blob], newName, {
                type: "image/jpeg",
                lastModified: Date.now(),
              }),
            );
          },
          "image/jpeg",
          quality,
        );
      } catch {
        cleanup();
        resolve(file);
      }
    };

    img.onerror = () => {
      cleanup();
      resolve(file);
    };

    img.src = objectUrl;
  });
}

/**
 * Ensure an array of image files are downscaled to max dimension.
 */
export async function ensureImagesResized(
  files: File[],
  maxDim: number = MAX_IMAGE_DIMENSION,
  quality: number = RESIZE_QUALITY,
): Promise<File[]> {
  return Promise.all(files.map((file) => resizeImageFile(file, maxDim, quality)));
}
