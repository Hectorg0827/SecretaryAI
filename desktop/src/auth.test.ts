import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock the Tauri IPC bridge (hoisted above imports by vitest).
const invokeMock = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({ invoke: (...a: any[]) => invokeMock(...a) }));

import { saveToken, clearToken, currentToken, authFetch, login } from './auth';

const SERVICE = 'secretary-auth';
const KEY = 'token';

describe('desktop auth (OS-vault token)', () => {
  beforeEach(async () => {
    vi.unstubAllGlobals();
    invokeMock.mockReset();
    invokeMock.mockResolvedValue(undefined);
    await clearToken();
    invokeMock.mockClear();
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

  it('completes 2FA with pre-auth token and code, without resending the password', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ access_token: 'step2-token' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    await expect(login('user@example.test', 'password-should-not-leak', {
      preAuthToken: 'short-lived', totpCode: '123456',
    })).resolves.toEqual({ ok: true });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      pre_auth_token: 'short-lived', totp_code: '123456',
    });
  });

  it('rejects invalid second-factor input before making a network request', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await expect(login('user@example.test', 'pw', {
      preAuthToken: 'short-lived', totpCode: 'not-a-code',
    })).rejects.toThrow('six-digit');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('fails sign-in closed if the OS vault cannot persist the access token', async () => {
    invokeMock.mockRejectedValueOnce(new Error('vault is locked'));
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ access_token: 'never-cached' }),
    }));
    await expect(login('user@example.test', 'pw')).rejects.toThrow('Secure credential storage');
    expect(currentToken()).toBeNull();
  });

  it('does not claim sign-out succeeded if the OS vault still contains a session', async () => {
    await saveToken('still-in-vault');
    invokeMock.mockRejectedValueOnce(new Error('vault is locked'));
    await expect(clearToken()).rejects.toThrow('Could not remove');
    expect(currentToken()).toBe('still-in-vault');
  });

  it('rejects a malformed MFA challenge instead of displaying a broken code entry', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ requires_2fa: true }),
    }));
    await expect(login('user@example.test', 'pw')).rejects.toThrow('could not be started');
  });
});
