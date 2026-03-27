import { API_BASE, setToken, clearToken, apiFetch } from './client';

export interface LoginResponse {
  access_token: string;
  token_type: string;
  company_id: string;
  role: string;
}

export interface Requires2FAResponse {
  requires_2fa: true;
  pre_auth_token: string;
}

export interface UserProfile {
  id: string;
  email: string;
  name: string;
  role: string;
  company_id: string;
  company_name: string;
  totp_enabled?: boolean;
}

export interface TwoFASetupResponse {
  secret: string;
  otpauth_url: string;
}

export async function login(
  email: string,
  password: string,
): Promise<LoginResponse | Requires2FAResponse> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });

  if (!res.ok) {
    let message = 'Invalid email or password';
    try {
      const err = await res.json();
      message = err.detail ?? message;
    } catch {}
    throw new Error(message);
  }

  const data = await res.json();
  if (data.requires_2fa) {
    return data as Requires2FAResponse;
  }
  await setToken((data as LoginResponse).access_token);
  return data as LoginResponse;
}

export async function loginWithTotp(
  preAuthToken: string,
  totpCode: string,
): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // The backend re-authenticates via TOTP; we send the pre_auth_token as
    // a stand-in for the credentials so the server can identify the user.
    body: JSON.stringify({ pre_auth_token: preAuthToken, totp_code: totpCode }),
  });

  if (!res.ok) {
    let message = 'Invalid 2FA code';
    try {
      const err = await res.json();
      message = err.detail ?? message;
    } catch {}
    throw new Error(message);
  }

  const data: LoginResponse = await res.json();
  await setToken(data.access_token);
  return data;
}

export async function logout(): Promise<void> {
  await clearToken();
}

export async function getMe(): Promise<UserProfile> {
  return apiFetch<UserProfile>('/auth/me');
}

export async function setup2fa(): Promise<TwoFASetupResponse> {
  return apiFetch<TwoFASetupResponse>('/auth/2fa/setup', { method: 'POST' });
}

export async function verify2fa(code: string): Promise<{ enabled: boolean }> {
  return apiFetch<{ enabled: boolean }>('/auth/2fa/verify', {
    method: 'POST',
    body: JSON.stringify({ code }),
  });
}

export async function disable2fa(code: string): Promise<{ enabled: boolean }> {
  return apiFetch<{ enabled: boolean }>('/auth/2fa/disable', {
    method: 'DELETE',
    body: JSON.stringify({ code }),
  });
}

/** Decode the role claim from a JWT without verifying the signature */
export function decodeTokenRole(token: string): string | null {
  try {
    const payload = token.split('.')[1];
    const decoded = JSON.parse(atob(payload));
    return decoded.role ?? null;
  } catch {
    return null;
  }
}
