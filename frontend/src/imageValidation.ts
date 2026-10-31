/**
 * Client-side label-image validation. Mirrors the server allowlist
 * (type, extension, and magic bytes) so a .txt or spoofed image is
 * rejected on drop. The server still re-checks every upload.
 */

const ALLOWED_EXTENSIONS = new Set([
  ".jpg",
  ".jpeg",
  ".png",
  ".webp",
  ".heic",
  ".heif",
]);

const ALLOWED_MIME_TYPES = new Set([
  "image/jpeg",
  "image/jpg",
  "image/png",
  "image/webp",
  "image/heic",
  "image/heif",
  "image/heic-sequence",
]);

/** Desktop browsers often tag HEIC/HEIF as generic binary, not image/heic. */
const GENERIC_BINARY_MIME_TYPES = new Set([
  "application/octet-stream",
  "binary/octet-stream",
]);

const IMAGE_SIGNATURES: Uint8Array[] = [
  new Uint8Array([0xff, 0xd8, 0xff]), // JPEG
  new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), // PNG
];

const HEIF_BRANDS = new Set([
  "heic",
  "heix",
  "heis",
  "hevc",
  "hevx",
  "heim",
  "heif",
  "mif1",
  "msf1",
]);

export const UNSUPPORTED_IMAGE_MESSAGE =
  "This file is not a supported label photo. Please upload a JPEG, PNG, WEBP, or HEIC image.";

export function fileExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

function startsWithBytes(data: Uint8Array, signature: Uint8Array): boolean {
  if (data.length < signature.length) return false;
  return signature.every((byte, index) => data[index] === byte);
}

function asciiAt(data: Uint8Array, start: number, length: number): string {
  return String.fromCharCode(...data.slice(start, start + length));
}

export function bytesLookLikeImage(data: Uint8Array): boolean {
  if (data.length >= 12 && asciiAt(data, 0, 4) === "RIFF" && asciiAt(data, 8, 4) === "WEBP") {
    return true;
  }
  if (data.length >= 12 && asciiAt(data, 4, 4) === "ftyp" && HEIF_BRANDS.has(asciiAt(data, 8, 4))) {
    return true;
  }
  return IMAGE_SIGNATURES.some((signature) => startsWithBytes(data, signature));
}

/**
 * Return a friendly error when the file is not a supported still image.
 * Empty MIME (common on camera captures) is allowed when the extension
 * or magic bytes identify the file.
 */
export async function validateImageFile(file: File): Promise<string | null> {
  const ext = fileExtension(file.name);
  const mime = (file.type || "").toLowerCase();

  if (ext && !ALLOWED_EXTENSIONS.has(ext)) {
    return UNSUPPORTED_IMAGE_MESSAGE;
  }
  if (
    mime &&
    !mime.startsWith("image/") &&
    !ALLOWED_MIME_TYPES.has(mime) &&
    !GENERIC_BINARY_MIME_TYPES.has(mime)
  ) {
    return UNSUPPORTED_IMAGE_MESSAGE;
  }
  if (mime && mime.startsWith("image/") && !ALLOWED_MIME_TYPES.has(mime) && mime !== "image/*") {
    return UNSUPPORTED_IMAGE_MESSAGE;
  }

  try {
    const buffer = await file.slice(0, 16).arrayBuffer();
    if (!bytesLookLikeImage(new Uint8Array(buffer))) {
      return UNSUPPORTED_IMAGE_MESSAGE;
    }
  } catch {
    return UNSUPPORTED_IMAGE_MESSAGE;
  }
  return null;
}
