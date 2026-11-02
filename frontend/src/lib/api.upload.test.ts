import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';

/** Multipart uploads: the browser must set the boundary, so no JSON Content-Type. */

function jsonResponse(status: number, body: unknown): Response {
  return { status, ok: status >= 200 && status < 300, json: async () => body } as unknown as Response;
}

function headersOf(init: RequestInit): Record<string, string> {
  return init.headers as Record<string, string>;
}

function form(): FormData {
  const data = new FormData();
  data.append('file', new File([new Uint8Array([37, 80, 68, 70])], 'invoice.pdf', { type: 'application/pdf' }));
  return data;
}

describe('api.upload', () => {
  beforeEach(() => localStorage.setItem('secretary_token', 'old-token'));
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('POSTs the FormData unchanged without a Content-Type header', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { bytes_received: 4 }));
    vi.stubGlobal('fetch', fetchMock);
    const body = form();

    await expect(api.upload('/api/logistics/evidence/e1/upload', body)).resolves.toEqual({ bytes_received: 4 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/logistics/evidence/e1/upload');
    expect(init.method).toBe('POST');
    expect(init.body).toBe(body);
    expect(Object.keys(headersOf(init)).map((key) => key.toLowerCase())).not.toContain('content-type');
    expect(headersOf(init).Authorization).toBe('Bearer old-token');
  });

  it('re-sends the same FormData without a Content-Type header after a token refresh', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      .mockResolvedValueOnce(jsonResponse(200, { access_token: 'new-token' }))
      .mockResolvedValueOnce(jsonResponse(201, { bytes_received: 4 }));
    vi.stubGlobal('fetch', fetchMock);
    const body = form();

    await expect(api.upload('/api/logistics/evidence/e1/upload', body)).resolves.toEqual({ bytes_received: 4 });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1][0]).toBe('/auth/refresh');
    const [url, init] = fetchMock.mock.calls[2];
    expect(url).toBe('/api/logistics/evidence/e1/upload');
    expect(init.method).toBe('POST');
    expect(init.body).toBe(body);
    expect(Object.keys(headersOf(init)).map((key) => key.toLowerCase())).not.toContain('content-type');
    expect(headersOf(init).Authorization).toBe('Bearer new-token');
  });

  it('surfaces the server refusal instead of reporting an upload', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(422, { detail: 'Evidence SHA-256 mismatch' })));
    await expect(api.upload('/api/logistics/evidence/e1/upload', form())).rejects.toThrow('Evidence SHA-256 mismatch');
  });

  it('still sends JSON bodies with a JSON Content-Type', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    await api.post('/api/billing/checkout', { plan_id: 'growth' });
    const init = fetchMock.mock.calls[0][1];
    expect(headersOf(init)['Content-Type']).toBe('application/json');
    expect(init.body).toBe(JSON.stringify({ plan_id: 'growth' }));
  });
});
