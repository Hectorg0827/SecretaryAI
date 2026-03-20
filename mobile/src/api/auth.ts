import { API_BASE, setToken, clearToken, apiFetch } from './client';

export interface LoginResponse {
  access_token: string;
  token_type: string;
  company_id: string;
  role: string;
}

export interface UserProfile {
  id: string;
  email: string;
  name: string;
  role: string;
  company_id: string;
  company_name: string;
}

export async function login(email: string, password: string): Promise<LoginResponse> {
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
