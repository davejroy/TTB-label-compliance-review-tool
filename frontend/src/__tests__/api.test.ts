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
    afterEach(() => {
        vi.restoreAllMocks();
        sessionStorage.clear();
    });

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

           it('uploads the original File without client-side resize', async () => {
                 const fetchMock = vi.fn().mockResolvedValue(makeResponse(200, {}));
                 vi.stubGlobal('fetch', fetchMock);
                 const { reviewLabel } = await import('../api');
                 const original = new File(['twelve-megapixel'], 'label.jpg', { type: 'image/jpeg' });
                 const mockApp = { beverage_type: 'distilled_spirits' as const, brand_name: 'Test',
                                        class_type: 'Bourbon', alcohol_content: '45%', net_contents: '750 mL' };
                 await reviewLabel([original], mockApp);
                 const body = fetchMock.mock.calls[0][1]?.body as FormData;
                 const uploaded = body.get('files') as File;
                 expect(uploaded.name).toBe('label.jpg');
                 expect(uploaded.size).toBe(original.size);
           });

           it('surfaces a clear 413 payload-too-large message without retrying', async () => {
                 vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
                         makeResponse(413, { error_code: 'FILE_TOO_LARGE', message: 'The upload is too large. Please use fewer or smaller photos.' })));
                 const { reviewLabel } = await import('../api');
                 const mockFile = new File(['img'], 'label.jpg', { type: 'image/jpeg' });
                 const mockApp = { beverage_type: 'distilled_spirits' as const, brand_name: 'Test',
                                        class_type: 'Bourbon', alcohol_content: '45%', net_contents: '750 mL' };
                 const err = await reviewLabel([mockFile], mockApp).catch((e: unknown) => e);
                 expect(err).toMatchObject({
                       status: 413,
                       error_code: 'FILE_TOO_LARGE',
                 });
                 expect(err).toBeInstanceOf(Error);
                 expect((err as Error).message).toBe(
                       'The upload is too large. Please use fewer or smaller photos.',
                 );
                 expect((err as Error).message).not.toMatch(/review failed/i);
           });

           it('does not show a proxy HTML 413 body verbatim', async () => {
                 vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
                         makeTextResponse(413, '<html><h1>413 Request Entity Too Large</h1></html>')));
                 const { reviewLabel } = await import('../api');
                 const mockFile = new File(['img'], 'label.jpg', { type: 'image/jpeg' });
                 const mockApp = { beverage_type: 'distilled_spirits' as const, brand_name: 'Test',
                                        class_type: 'Bourbon', alcohol_content: '45%', net_contents: '750 mL' };
                 const err = await reviewLabel([mockFile], mockApp).catch((e: unknown) => e);
                 expect(err).toMatchObject({ status: 413 });
                 expect((err as Error).message).toMatch(/fewer photos or smaller files/i);
                 expect((err as Error).message).not.toMatch(/<html/i);
                 expect((err as Error).message).not.toMatch(/request entity too large/i);
           });

           it('surfaces the API FILE_TOO_LARGE text on batch 413, not the 10 MB generic', async () => {
                 vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
                         makeResponse(413, { error_code: 'FILE_TOO_LARGE', message: 'The upload is too large. Please use fewer or smaller photos.' })));
                 const { reviewLabelsBatch } = await import('../api');
                 const mockFile = new File(['img'], 'label.jpg', { type: 'image/jpeg' });
                 const mockApp = { beverage_type: 'distilled_spirits' as const, brand_name: 'Test',
                                        class_type: 'Bourbon', alcohol_content: '45%', net_contents: '750 mL' };
                 const err = await reviewLabelsBatch([{ files: [mockFile], application: mockApp }]).catch((e: unknown) => e);
                 expect(err).toMatchObject({ status: 413, error_code: 'FILE_TOO_LARGE' });
                 expect((err as Error).message).toBe(
                       'The upload is too large. Please use fewer or smaller photos.',
                 );
                 expect((err as Error).message).not.toMatch(/batch review failed/i);
                 expect((err as Error).message).not.toMatch(/each photo can be up to 10 MB/i);
           });

           it('surfaces the API FILE_TOO_LARGE text on batch stream 413', async () => {
                 vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
                         makeResponse(413, { error_code: 'FILE_TOO_LARGE', message: 'The upload is too large. Please use fewer or smaller photos.' })));
                 const { reviewLabelsBatchStream } = await import('../api');
                 const mockFile = new File(['img'], 'label.jpg', { type: 'image/jpeg' });
                 const mockApp = { beverage_type: 'distilled_spirits' as const, brand_name: 'Test',
                                        class_type: 'Bourbon', alcohol_content: '45%', net_contents: '750 mL' };
                 const err = await reviewLabelsBatchStream(
                       [{ files: [mockFile], application: mockApp }],
                       () => undefined,
                 ).catch((e: unknown) => e);
                 expect(err).toMatchObject({ status: 413, error_code: 'FILE_TOO_LARGE' });
                 expect((err as Error).message).toBe(
                       'The upload is too large. Please use fewer or smaller photos.',
                 );
                 expect((err as Error).message).not.toMatch(/batch stream failed/i);
                 expect((err as Error).message).not.toMatch(/each photo can be up to 10 MB/i);
           });

           it('surfaces a clear 401 unauthorized message', async () => {
                 vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
                         makeResponse(401, { error_code: 'AUTH_REQUIRED', message: 'Invalid or missing access token.' })));
                 const { reviewLabel } = await import('../api');
                 const mockFile = new File(['img'], 'label.jpg', { type: 'image/jpeg' });
                 const mockApp = { beverage_type: 'distilled_spirits' as const, brand_name: 'Test',
                                        class_type: 'Bourbon', alcohol_content: '45%', net_contents: '750 mL' };
                 await expect(reviewLabel([mockFile], mockApp)).rejects.toThrow(/not authorized/i);
           });

           it('clears the stored demo token on 401', async () => {
                 sessionStorage.setItem('ttb_demo_token', 'stale-token');
                 vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
                         makeResponse(401, { error_code: 'AUTH_REQUIRED', message: 'Invalid or missing access token.' })));
                 const { reviewLabel } = await import('../api');
                 const mockFile = new File(['img'], 'label.jpg', { type: 'image/jpeg' });
                 const mockApp = { beverage_type: 'distilled_spirits' as const, brand_name: 'Test',
                                        class_type: 'Bourbon', alcohol_content: '45%', net_contents: '750 mL' };
                 await expect(reviewLabel([mockFile], mockApp)).rejects.toMatchObject({ status: 401 });
                 expect(sessionStorage.getItem('ttb_demo_token')).toBeNull();
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

// COLA Presets & Template Import API
describe('COLA Presets and Template Import API', () => {
    afterEach(() => vi.restoreAllMocks());

    it('getColaPresets calls /api/cola/presets and returns preset list', async () => {
        const mockPresets = [
            {
                id: 'preset_bourbon',
                name: 'Bourbon',
                description: 'Spirits',
                application: {
                    beverage_type: 'distilled_spirits',
                    brand_name: 'Old Tom',
                    class_type: 'Bourbon',
                    alcohol_content: '45%',
                    net_contents: '750 mL',
                    name_and_address: 'Bardstown, KY',
                    country_of_origin: null,
                },
            },
        ];
        const fetchMock = vi.fn().mockResolvedValue(makeResponse(200, mockPresets));
        vi.stubGlobal('fetch', fetchMock);
        const { getColaPresets } = await import('../api');
        const result = await getColaPresets();
        expect(result).toEqual(mockPresets);
        expect(fetchMock).toHaveBeenCalledWith(
            expect.stringContaining('/api/cola/presets'),
            expect.objectContaining({ method: 'GET' }),
        );
    });

    it('parseColaTemplate posts file to /api/cola/parse-template and returns parsed applications', async () => {
        const mockParsed = [
            {
                beverage_type: 'wine',
                brand_name: 'Silver Oak',
                class_type: 'Cabernet',
                alcohol_content: '14.2%',
                net_contents: '750 mL',
                name_and_address: 'Oakville, CA',
                country_of_origin: null,
            },
        ];
        const fetchMock = vi.fn().mockResolvedValue(makeResponse(200, mockParsed));
        vi.stubGlobal('fetch', fetchMock);
        const { parseColaTemplate } = await import('../api');
        const file = new File(['beverage_type,brand_name\nwine,Silver Oak'], 'test.csv', { type: 'text/csv' });
        const result = await parseColaTemplate(file);
        expect(result).toEqual(mockParsed);
        expect(fetchMock).toHaveBeenCalledWith(
            expect.stringContaining('/api/cola/parse-template'),
            expect.objectContaining({ method: 'POST' }),
        );
    });

    it('parseColaTemplate surfaces 422 validation detail on bad template files', async () => {
        vi.stubGlobal(
            'fetch',
            vi.fn().mockResolvedValue(
                makeResponse(422, { detail: 'Missing required application field(s): brand_name' }),
            ),
        );
        const { parseColaTemplate } = await import('../api');
        const file = new File(['bad_content'], 'bad.csv', { type: 'text/csv' });
        await expect(parseColaTemplate(file)).rejects.toThrow('Missing required application field(s): brand_name');
    });
});


