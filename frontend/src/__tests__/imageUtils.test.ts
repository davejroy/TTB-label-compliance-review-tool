import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  calculateTargetDimensions,
  resizeImageFile,
  ensureImagesResized,
  MAX_IMAGE_DIMENSION,
} from '../imageUtils';

describe('imageUtils - calculateTargetDimensions', () => {
  it('does not scale images within max dimension', () => {
    const result = calculateTargetDimensions(1200, 800, 1600);
    expect(result).toEqual({ width: 1200, height: 800, scaled: false });
  });

  it('does not scale images exactly at max dimension', () => {
    const result = calculateTargetDimensions(1600, 1200, 1600);
    expect(result).toEqual({ width: 1600, height: 1200, scaled: false });
  });

  it('scales down landscape images exceeding max dimension', () => {
    const result = calculateTargetDimensions(3200, 1600, 1600);
    expect(result).toEqual({ width: 1600, height: 800, scaled: true });
  });

  it('scales down portrait images exceeding max dimension', () => {
    const result = calculateTargetDimensions(1500, 3000, 1600);
    expect(result).toEqual({ width: 800, height: 1600, scaled: true });
  });

  it('scales down square images', () => {
    const result = calculateTargetDimensions(4000, 4000, 1600);
    expect(result).toEqual({ width: 1600, height: 1600, scaled: true });
  });

  it('handles zero or negative dimensions safely', () => {
    expect(calculateTargetDimensions(0, 0, 1600)).toEqual({ width: 0, height: 0, scaled: false });
    expect(calculateTargetDimensions(-10, 50, 1600)).toEqual({ width: -10, height: 50, scaled: false });
  });

  it('defaults maxDim to MAX_IMAGE_DIMENSION (1600)', () => {
    expect(MAX_IMAGE_DIMENSION).toBe(1600);
    const result = calculateTargetDimensions(3200, 1600);
    expect(result.width).toBe(1600);
    expect(result.height).toBe(800);
    expect(result.scaled).toBe(true);
  });
});

describe('imageUtils - resizeImageFile', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns non-image files unchanged (fail-open)', async () => {
    const textFile = new File(['hello'], 'test.txt', { type: 'text/plain' });
    const result = await resizeImageFile(textFile);
    expect(result).toBe(textFile);
  });

  it('returns original file fail-open when image decode times out or fails', async () => {
    const imageFile = new File(['dummy bytes'], 'label.jpg', { type: 'image/jpeg' });
    const result = await resizeImageFile(imageFile);
    expect(result).toBe(imageFile);
  });

  it('processes multiple files in parallel with ensureImagesResized', async () => {
    const file1 = new File(['bytes1'], 'label1.png', { type: 'image/png' });
    const file2 = new File(['bytes2'], 'label2.jpg', { type: 'image/jpeg' });
    const results = await ensureImagesResized([file1, file2]);
    expect(results).toHaveLength(2);
  });
});
