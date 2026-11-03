import { describe, it, expect } from "vitest";
import {
  bytesLookLikeImage,
  fileExtension,
  UNSUPPORTED_IMAGE_MESSAGE,
  validateImageFile,
} from "../imageValidation";

const PNG_MAGIC = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00]);
const JPEG_MAGIC = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10]);
const HEIC_MAGIC = new Uint8Array([
  0x00, 0x00, 0x00, 0x18, 0x66, 0x74, 0x79, 0x70, 0x68, 0x65, 0x69, 0x63,
]);

describe("fileExtension", () => {
  it("returns the lowercased suffix including the dot", () => {
    expect(fileExtension("Front.PNG")).toBe(".png");
    expect(fileExtension("notes.txt")).toBe(".txt");
    expect(fileExtension("noext")).toBe("");
  });
});

describe("bytesLookLikeImage", () => {
  it("accepts JPEG, PNG, WEBP, and HEIC signatures", () => {
    expect(bytesLookLikeImage(PNG_MAGIC)).toBe(true);
    expect(bytesLookLikeImage(JPEG_MAGIC)).toBe(true);
    const webp = new Uint8Array(12);
    webp.set([0x52, 0x49, 0x46, 0x46], 0);
    webp.set([0x57, 0x45, 0x42, 0x50], 8);
    expect(bytesLookLikeImage(webp)).toBe(true);
    expect(bytesLookLikeImage(HEIC_MAGIC)).toBe(true);
  });

  it("rejects plain text and empty buffers", () => {
    expect(bytesLookLikeImage(new TextEncoder().encode("hello"))).toBe(false);
    expect(bytesLookLikeImage(new Uint8Array())).toBe(false);
  });
});

describe("validateImageFile", () => {
  it("rejects .txt even when the MIME type is spoofed as image/png", async () => {
    const file = new File([PNG_MAGIC], "notes.txt", { type: "image/png" });
    expect(await validateImageFile(file)).toBe(UNSUPPORTED_IMAGE_MESSAGE);
  });

  it("rejects GIF even when the signature is valid", async () => {
    const gif = new Uint8Array([0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x00]);
    const file = new File([gif], "anim.gif", { type: "image/gif" });
    expect(await validateImageFile(file)).toBe(UNSUPPORTED_IMAGE_MESSAGE);
    expect(bytesLookLikeImage(gif)).toBe(false);
  });

  it("rejects a fake PNG whose magic bytes are text", async () => {
    const file = new File(["not-an-image"], "front.png", { type: "image/png" });
    expect(await validateImageFile(file)).toBe(UNSUPPORTED_IMAGE_MESSAGE);
  });

  it("accepts a real PNG signature", async () => {
    const file = new File([PNG_MAGIC], "front.png", { type: "image/png" });
    expect(await validateImageFile(file)).toBeNull();
  });

  it("accepts HEIC when the browser tags it as application/octet-stream", async () => {
    const file = new File([HEIC_MAGIC], "IMG_1234.heic", { type: "application/octet-stream" });
    expect(await validateImageFile(file)).toBeNull();
  });

  it("still rejects a .txt file tagged as application/octet-stream", async () => {
    const file = new File(["hello"], "notes.txt", { type: "application/octet-stream" });
    expect(await validateImageFile(file)).toBe(UNSUPPORTED_IMAGE_MESSAGE);
  });
});
