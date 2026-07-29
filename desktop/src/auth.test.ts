import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock the Tauri IPC bridge (hoisted above imports by vitest).
const invokeMock = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({ invoke: (...a: any[]) => invokeMock(...a) }));

import { saveToken, currentToken, authFetch, login } from './auth';

const SERVICE = 'secretary-auth';
const KEY = 'token';

describe('desktop auth (OS-vault token)', () => {
  beforeEach(() => {
    invokeMock.mockReset();
    invokeMock.mockResolvedValue(undefined);
  });

  it('saveToken persists to the credential vault and caches in memory', async () => {
    await saveToken('tok123');
    expect(invokeMock).toHaveBeenCalledWith('store_credential', {
      service: SERVICE, key: KEY, value: 'tok123',
    });
    expect(currentToken()).toBe('tok123');
  });

  it('authFetch adds the bearer token and refreshes once on 401', async () => {
    await saveToken('old');
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ status: 401, ok: false })                              // original
      .mockResolvedValueOnce({ ok: true, json: async () => ({ access_token: 'new' }) }) // refresh
      .mockResolvedValueOnce({ status: 200, ok: true });                              // retry
    vi.stubGlobal('fetch', fetchMock);

    const res = await authFetch('/api/x');
    expect(res.ok).toBe(true);
    expect(fetchMock.mock.calls[2][1].headers.Authorization).toBe('Bearer new');
    expect(currentToken()).toBe('new'); // rotated token cached + persisted
  });

  it('login stores the token on success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce({
      ok: true, json: async () => ({ access_token: 'logintok' }),
    }));
    const r = await login('a@b.com', 'pw');
    expect(r).toEqual({ ok: true });
    expect(currentToken()).toBe('logintok');
  });

  it('login surfaces the 2FA step', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce({
      ok: true, json: async () => ({ requires_2fa: true, pre_auth_token: 'pat' }),
    }));
    const r = await login('a@b.com', 'pw');
    expect(r).toEqual({ requires2fa: true, preAuthToken: 'pat' });
  });
});
