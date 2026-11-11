/// <reference types="vite/client" />
/**
 * Where the web app keeps its sign-in.
 *
 * Cookie mode (preferred): the server puts the session token in an httpOnly
 * cookie. Scripts on the page — including a malicious one — cannot read it.
 * The page only keeps things that are not secrets: a "signed in" flag and the
 * role (used to decide which buttons to show; the server enforces the real
 * rules), plus a CSRF value that is useless without the cookie.
 *
 * Token mode (fallback): the token is kept in localStorage and sent as a
 * Bearer header. Used only when the API is on a different site from the web
 * app, where browsers increasingly refuse to send cookies.
 *
 * Which mode: `VITE_SESSION_MODE=cookie|token` if set; otherwise cookie mode
 * whenever the API is served from the same origin as the page (the default
 * deployment, where nginx forwards /api and /auth). Set
 * `VITE_SESSION_MODE=cookie` when the API is on a sibling subdomain
 * (app.example.com + api.example.com).
 */

export const API_BASE: string = import.meta.env.VITE_API_URL ?? '';

const TOKEN_KEY = 'secretary_token';
const ROLE_KEY = 'secretary_role';
const COMPANY_KEY = 'secretary_company_id';
const SIGNED_IN_KEY = 'secretary_signed_in';
const CSRF_KEY = 'secretary_csrf';

export type SessionMode = 'cookie' | 'token';

function decideMode(): SessionMode {
  const forced = import.meta.env.VITE_SESSION_MODE;
  if (forced === 'cookie' || forced === 'token') return forced;
  if (!API_BASE) return 'cookie';
  try {
    return new URL(API_BASE, window.location.href).origin === window.location.origin ? 'cookie' : 'token';
  } catch {
    return 'token';
  }
}

let mode: SessionMode = decideMode();

export function sessionMode(): SessionMode { return mode; }
/** Tests only. */
export function setSessionMode(m: SessionMode): void { mode = m; }

export interface SessionReply {
  access_token?: string;
  csrf_token?: string | null;
  role?: string;
  company_id?: string;
}

/** Remember a new session (after sign-in, sign-up, refresh, password change). */
export function saveSession(reply: SessionReply): void {
  if (mode === 'cookie') {
    // The token is in an httpOnly cookie; make sure no old copy lingers here.
    localStorage.removeItem(TOKEN_KEY);
    localStorage.setItem(SIGNED_IN_KEY, '1');
    if (reply.csrf_token) sessionStorage.setItem(CSRF_KEY, reply.csrf_token);
  } else if (reply.access_token) {
    localStorage.setItem(TOKEN_KEY, reply.access_token);
  }
  if (reply.role) localStorage.setItem(ROLE_KEY, reply.role);
  if (reply.company_id) localStorage.setItem(COMPANY_KEY, reply.company_id);
}

export function clearSession(): void {
  for (const k of [TOKEN_KEY, ROLE_KEY, COMPANY_KEY, SIGNED_IN_KEY]) localStorage.removeItem(k);
  sessionStorage.removeItem(CSRF_KEY);
}

export function isSignedIn(): boolean {
  return mode === 'cookie' ? localStorage.getItem(SIGNED_IN_KEY) === '1' : !!localStorage.getItem(TOKEN_KEY);
}

export function getToken(): string | null {
  return mode === 'token' ? localStorage.getItem(TOKEN_KEY) : null;
}

export function getCsrf(): string | null {
  return sessionStorage.getItem(CSRF_KEY);
}

function roleFromToken(token: string): string | null {
  try {
    const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const payload = JSON.parse(atob(base64));
    return typeof payload.role === 'string' ? payload.role : null;
  } catch {
    return null;
  }
}

/**
 * The signed-in user's role, for showing or hiding screens. In token mode it
 * is read from the signed token; in cookie mode from what the server told us
 * at sign-in. Either way it only affects what is displayed — every request is
 * checked on the server against the real session.
 */
export function currentRole(): string {
  if (mode === 'token') {
    const token = localStorage.getItem(TOKEN_KEY);
    return (token && roleFromToken(token)) || 'viewer';
  }
  return isSignedIn() ? localStorage.getItem(ROLE_KEY) || 'viewer' : 'viewer';
}

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

/** Headers that authenticate a request in the current mode. */
export function authHeaders(method: string): Record<string, string> {
  if (mode === 'token') {
    const token = getToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }
  const headers: Record<string, string> = { 'X-Session-Mode': 'cookie' };
  const csrf = getCsrf();
  if (csrf && !SAFE_METHODS.has(method.toUpperCase())) headers['X-CSRF-Token'] = csrf;
  return headers;
}

/** fetch() options that make the browser send the session cookie. */
export function credentialOptions(): { credentials: RequestCredentials } {
  return { credentials: mode === 'cookie' ? 'include' : 'same-origin' };
}

let bootstrapping: Promise<boolean> | null = null;

/**
 * Cookie mode, new tab or reload: the cookie is there but this tab has no CSRF
 * value yet. Ask the server who we are. Returns false if not signed in.
 */
export function ensureSession(): Promise<boolean> {
  if (mode !== 'cookie') return Promise.resolve(isSignedIn());
  if (!isSignedIn()) return Promise.resolve(false);
  if (getCsrf()) return Promise.resolve(true);
  if (!bootstrapping) {
    bootstrapping = fetch(`${API_BASE}/auth/session`, { headers: { 'X-Session-Mode': 'cookie' }, credentials: 'include' })
      .then(async (res) => {
        if (!res.ok) return false;
        saveSession(await res.json());
        return true;
      })
      .catch(() => false)
      .finally(() => { bootstrapping = null; });
  }
  return bootstrapping;
}
