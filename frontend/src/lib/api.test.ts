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
