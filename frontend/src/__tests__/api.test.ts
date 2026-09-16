/**
 * Tests for src/api.ts — safeFetch retry, URL construction,
 * parseErrorBody, and /api prefix regression guard (commit 1eea52f).
 * All fetch calls mocked via vi.stubGlobal. No network or API key needed.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

function makeResponse(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
function makeTextResponse(status: number, text: string): Response {
  return new Response(text, { status });
}

// /api prefix regression guard (commit 1eea52f)
describe('checkLabelsBatch URL', () => {
  afterEach(() => vi.restoreAllMocks());

  it('calls /api/label-check/batch — not /label-check/batch', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeResponse(200, []));
    vi.stubGlobal('fetch', fetchMock);
    const { checkLabelsBatch } = await import('../api');
    const mockFile = new File(['img'], 'label.jpg', { type: 'image/jpeg' });
    await checkLabelsBatch([{ files: [mockFile] }]);
    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).toContain('/api/label-check/batch');
    expect(calledUrl).not.toMatch(/^\/label-check/);
  });
});

// wakeServerIfNeeded
describe('wakeServerIfNeeded', () => {
  afterEach(() => vi.restoreAllMocks());

  it('returns "warm" when health endpoint responds quickly', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeResponse(200, { status: 'ok' }));
    vi.stubGlobal('fetch', fetchMock);
    const { wakeServerIfNeeded } = await import('../api');
    const result = await wakeServerIfNeeded();
    expect(result).toBe('warm');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/health'),
      expect.any(Object),
    );
  });

  it('throws user-friendly message on TypeError (network error)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed')));
    const { wakeServerIfNeeded } = await import('../api');
    await expect(wakeServerIfNeeded()).rejects.toThrow('Could not reach the review server');
  });
});

// safeFetch retry logic
describe('safeFetch retry', () => {
  afterEach(() => vi.restoreAllMocks());

  it('retries once on 429 and returns second response on success', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makeResponse(429))
      .mockResolvedValueOnce(makeResponse(200, []));
    vi.stubGlobal('fetch', fetchMock);
    const { reviewLabelsBatch } = await import('../api');
    const mockFile = new File(['img'], 'label.jpg', { type: 'image/jpeg' });
    const mockApp = { beverage_type: 'distilled_spirits' as const, brand_name: 'Test',
                      class_type: 'Bourbon', alcohol_content: '45%', net_contents: '750 mL' };
    await reviewLabelsBatch([{ files: [mockFile], application: mockApp }]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does NOT retry on 404', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeResponse(404, { detail: 'Not found' })));
    const { reviewLabel } = await import('../api');
    const mockFile = new File(['img'], 'label.jpg', { type: 'image/jpeg' });
    const mockApp = { beverage_type: 'distilled_spirits' as const, brand_name: 'Test',
                      class_type: 'Bourbon', alcohol_content: '45%', net_contents: '750 mL' };
    await expect(reviewLabel([mockFile], mockApp)).rejects.toThrow('404');
  });
});

// parseErrorBody
describe('error body parsing', () => {
  afterEach(() => vi.restoreAllMocks());

  it('extracts FastAPI JSON detail string', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      makeResponse(422, { detail: 'Image quality too low' })));
    const { reviewLabel } = await import('../api');
    const mockFile = new File(['img'], 'label.jpg', { type: 'image/jpeg' });
    const mockApp = { beverage_type: 'distilled_spirits' as const, brand_name: 'Test',
                      class_type: 'Bourbon', alcohol_content: '45%', net_contents: '750 mL' };
    await expect(reviewLabel([mockFile], mockApp)).rejects.toThrow('Image quality too low');
  });

  it('falls back to plain text for non-JSON error bodies', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeTextResponse(500, 'Server Error')));
    const { reviewLabel } = await import('../api');
    const mockFile = new File(['img'], 'label.jpg', { type: 'image/jpeg' });
    const mockApp = { beverage_type: 'distilled_spirits' as const, brand_name: 'Test',
                      class_type: 'Bourbon', alcohol_content: '45%', net_contents: '750 mL' };
    await expect(reviewLabel([mockFile], mockApp)).rejects.toThrow('Server Error');
  });
});

// authHeader inclusion
describe('authHeader propagation', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  it('includes Authorization header when token is stored', async () => {
    sessionStorage.setItem('ttb_demo_token', 'test-token-123');
    const fetchMock = vi.fn().mockResolvedValue(makeResponse(200, {}));
    vi.stubGlobal('fetch', fetchMock);
    const { reviewLabel } = await import('../api');
    const mockFile = new File(['img'], 'label.jpg', { type: 'image/jpeg' });
    const mockApp = { beverage_type: 'distilled_spirits' as const, brand_name: 'Test',
                      class_type: 'Bourbon', alcohol_content: '45%', net_contents: '750 mL' };
    await reviewLabel([mockFile], mockApp);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/review'),
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer test-token-123',
        }),
      }),
    );
  });

  it('does not include Authorization header when token is absent', async () => {
    sessionStorage.clear();
    const fetchMock = vi.fn().mockResolvedValue(makeResponse(200, {}));
    vi.stubGlobal('fetch', fetchMock);
    const { reviewLabel } = await import('../api');
    const mockFile = new File(['img'], 'label.jpg', { type: 'image/jpeg' });
    const mockApp = { beverage_type: 'distilled_spirits' as const, brand_name: 'Test',
                      class_type: 'Bourbon', alcohol_content: '45%', net_contents: '750 mL' };
    await reviewLabel([mockFile], mockApp);
    const callInit = fetchMock.mock.calls[0][1] as RequestInit;
    expect((callInit.headers as Record<string, string>)?.Authorization).toBeUndefined();
  });
});
