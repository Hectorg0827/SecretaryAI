/**
 * Desktop authentication + session token storage.
 *
 * The session token is persisted in the OS credential vault (Keychain on macOS,
 * Credential Manager on Windows) under the SAME key the Rust background sync
 * loop reads (`secretary-auth` / `token`) — NOT localStorage. This is the single
 * source of truth shared by the UI and the native agent. (Defect #5.)
 */
import { invoke } from '@tauri-apps/api/core';
import { API_URL } from './config';

const SERVICE = 'secretary-auth';
const KEY = 'token';

let _token: string | null = null;

/** Load the token (cached in memory, hydrated from the OS vault on first use). */
export async function loadToken(): Promise<string | null> {
  if (_token) return _token;
  try {
    _token = await invoke<string | null>('get_credential', { service: SERVICE, key: KEY });
  } catch {
    _token = null;
  }
  return _token;
}

/** Persist the token to the OS vault so the background agent can authenticate. */
export async function saveToken(token: string): Promise<void> {
  _token = token;
  try {
    await invoke('store_credential', { service: SERVICE, key: KEY, value: token });
  } catch (e) {
    // In-memory token still works this session, but background sync (which reads
    // the vault) won't be authenticated. Surface for diagnostics.
    console.error('Failed to persist session token to OS vault:', e);
  }
}

export async function clearToken(): Promise<void> {
  _token = null;
  try {
    await invoke('delete_credential', { service: SERVICE, key: KEY });
  } catch {
    /* best-effort */
  }
}

export function currentToken(): string | null {
  return _token;
}

/** Authenticated fetch against the backend, with a single refresh-retry on 401. */
export async function authFetch(path: string, opts: RequestInit = {}): Promise<Response> {
  const token = await loadToken();
  const call = (t: string | null) =>
    fetch(`${API_URL}${path}`, {
      ...opts,
      headers: {
        'Content-Type': 'application/json',
        ...(t ? { Authorization: `Bearer ${t}` } : {}),
        ...(opts.headers ?? {}),
      },
    });

  let res = await call(token);
  if (res.status === 401 && token) {
    const refreshed = await tryRefresh(token);
    if (refreshed) res = await call(refreshed);
  }
  return res;
}

async function tryRefresh(token: string): Promise<string | null> {
  try {
    const res = await fetch(`${API_URL}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ access_token: token }),
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (data?.access_token) {
      await saveToken(data.access_token);
      return data.access_token;
    }
  } catch {
    /* offline / network error */
  }
  return null;
}

export type LoginResult =
  | { ok: true }
  | { requires2fa: true; preAuthToken: string };

/**
 * Log in with email + password (+ optional TOTP). Handles the two-step 2FA flow:
 * the first call may return `requires_2fa` + a `pre_auth_token`; call again with
 * that token and the code.
 */
export async function login(
  email: string,
  password: string,
  opts: { totpCode?: string; preAuthToken?: string } = {},
): Promise<LoginResult> {
  const body: Record<string, unknown> = { email, password };
  if (opts.totpCode) body.totp_code = opts.totpCode;
  if (opts.preAuthToken) body.pre_auth_token = opts.preAuthToken;

  const res = await fetch(`${API_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.detail ?? `Login failed (${res.status})`);

  if (data?.requires_2fa) {
    return { requires2fa: true, preAuthToken: data.pre_auth_token };
  }
  if (data?.access_token) {
    await saveToken(data.access_token);
    return { ok: true };
  }
  throw new Error('Unexpected login response');
}
