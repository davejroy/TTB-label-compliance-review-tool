import { describe, it, expect, vi } from 'vitest';
import { createRef } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ImageDropzone, { type ImageDropzoneHandle } from '../components/ImageDropzone';

function pngFile(name: string): File {
  return new File(['fake-png'], name, { type: 'image/png' });
}

describe('ImageDropzone', () => {
  it('accepts HEIC/HEIF as well as JPEG, PNG, and WebP', () => {
    render(<ImageDropzone files={[]} onChange={() => undefined} idPrefix="accept" />);
    const input = document.getElementById('accept-file') as HTMLInputElement;
    expect(input.accept).toContain('image/heic');
    expect(input.accept).toContain('image/heif');
    expect(input.accept).toContain('.heic');
    expect(input.accept).toContain('image/jpeg');
  });

  it('keeps the original File object instead of a resized copy', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const original = pngFile('full-res.png');
    render(<ImageDropzone files={[]} onChange={onChange} idPrefix="orig" />);
    await user.upload(screen.getByLabelText(/Label Images/i), original);
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const next = onChange.mock.calls.at(-1)?.[0] as File[];
    expect(next).toHaveLength(1);
    expect(next[0]).toBe(original);
    expect(next[0].name).toBe('full-res.png');
  });

  it('clears the placeholder when a later file occupies the same slot', async () => {
    const heic = new File(['heic-bytes'], 'phone.heic', { type: 'image/heic' });
    const png = pngFile('front.png');
    const { rerender } = render(
      <ImageDropzone files={[heic]} onChange={() => undefined} idPrefix="swap" />,
    );
    fireEvent.error(screen.getByAltText(/front label image/i));
    expect(await screen.findByText('Preview not available')).toBeInTheDocument();

    rerender(<ImageDropzone files={[png]} onChange={() => undefined} idPrefix="swap" />);
    expect(screen.queryByText('Preview not available')).not.toBeInTheDocument();
    expect(screen.getByAltText(/front label image/i).tagName).toBe('IMG');
  });

  it('previews raster images with blob: object URLs', () => {
    const png = pngFile('front.png');
    render(<ImageDropzone files={[png]} onChange={() => undefined} idPrefix="blob" />);
    const img = screen.getByAltText(/front label image/i);
    expect(img.getAttribute('src')).toMatch(/^blob:/);
  });

  it('shows a placeholder when the browser cannot preview HEIC', async () => {
    const heic = new File(['heic-bytes'], 'phone.heic', { type: 'image/heic' });
    render(<ImageDropzone files={[heic]} onChange={() => undefined} idPrefix="heic" />);
    const img = screen.getByAltText(/front label image/i);
    fireEvent.error(img);
    expect(await screen.findByText('Preview not available')).toBeInTheDocument();
    expect(screen.getByText('phone.heic')).toBeInTheDocument();
  });

  it('appends a Choose File upload after a cancelled retake instead of replacing', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const existing = pngFile('front.png');
    const ref = createRef<ImageDropzoneHandle>();

    const { rerender } = render(
      <ImageDropzone ref={ref} files={[existing]} onChange={onChange} idPrefix="test" />,
    );

    expect(screen.getByText('front')).toBeInTheDocument();
    ref.current?.openCapture('front');

    await user.click(screen.getByRole('button', { name: 'Choose File' }));
    const incoming = pngFile('extra.png');
    await user.upload(screen.getByLabelText(/Label Images/i), incoming);

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const next = onChange.mock.calls.at(-1)?.[0] as File[];
    expect(next).toHaveLength(2);
    expect(next[0].name).toBe('front.png');
    expect(next[1].name).toBe('extra.png');

    rerender(
      <ImageDropzone ref={ref} files={next} onChange={onChange} idPrefix="test" />,
    );
    expect(screen.getByText('back')).toBeInTheDocument();
  });

  it('replaces the matching panel when the camera input is used after openCapture', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const front = pngFile('front.png');
    const back = pngFile('back.png');
    const ref = createRef<ImageDropzoneHandle>();

    render(
      <ImageDropzone ref={ref} files={[front, back]} onChange={onChange} idPrefix="cam" />,
    );

    ref.current?.openCapture('back');
    const replacement = pngFile('back-retake.png');
    const camera = document.getElementById('cam-camera') as HTMLInputElement;
    await user.upload(camera, replacement);

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const next = onChange.mock.calls.at(-1)?.[0] as File[];
    expect(next).toHaveLength(2);
    expect(next[0].name).toBe('front.png');
    expect(next[1].name).toBe('back-retake.png');
  });
});
