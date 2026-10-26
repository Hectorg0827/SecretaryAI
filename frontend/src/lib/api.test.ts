import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { api } from './api';

/**
 * Security-relevant behavior of the API client: on a 401 it silently refreshes
 * the token once and retries; if refresh fails it clears the token and bails.
 */

function jsonResponse(status: number, body: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  } as unknown as Response;
}

describe('api client 401 → refresh flow', () => {
  beforeEach(() => {
    localStorage.setItem('secretary_token', 'old-token');
  });
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('refreshes and retries once, updating the stored token', async () => {
    const fetchMock = vi
      .fn()
      // 1) original request → 401
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      // 2) /auth/refresh → new token
      .mockResolvedValueOnce(jsonResponse(200, { access_token: 'new-token' }))
      // 3) retry → success
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await api.get<{ ok: boolean }>('/api/dashboard/summary');

    expect(result).toEqual({ ok: true });
    expect(localStorage.getItem('secretary_token')).toBe('new-token');
    // 2nd call is the refresh; 3rd carries the new bearer token.
    const retryInit = fetchMock.mock.calls[2][1];
    expect(retryInit.headers.Authorization).toBe('Bearer new-token');
  });

  it('clears the token and throws when refresh fails', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      // refresh fails
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'nope' }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(api.get('/api/dashboard/summary')).rejects.toThrow();
    expect(localStorage.getItem('secretary_token')).toBeNull();
  });
});

describe('hosted API errors and session boundaries', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('uses same-origin URLs and displays failed login without refreshing or losing the form', async () => {
    localStorage.setItem('secretary_token', 'stale-token');
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(401, { detail: 'Invalid credentials' }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(api.auth.login('pilot@example.test', 'incorrect')).rejects.toThrow('Invalid credentials');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/auth/login');
  });

  it('shows field-validation messages instead of object strings', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(422, {
      detail: [{ msg: 'Password must contain at least one number' }],
    })));
    await expect(api.post('/auth/register', {})).rejects.toThrow('Password must contain at least one number');
  });

  it.each([402, 403, 503])('keeps the refreshed session when the retry returns %s', async (status) => {
    localStorage.setItem('secretary_token', 'old-token');
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      .mockResolvedValueOnce(jsonResponse(200, { access_token: 'new-token' }))
      .mockResolvedValueOnce(jsonResponse(status, { detail: 'Operation unavailable' })));
    await expect(api.get('/api/dashboard/summary')).rejects.toThrow('Operation unavailable');
    expect(localStorage.getItem('secretary_token')).toBe('new-token');
  });

  it('accepts a successful response without a JSON body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ status: 204, ok: true }));
    await expect(api.delete('/auth/qbo/disconnect')).resolves.toBeUndefined();
  });
});
