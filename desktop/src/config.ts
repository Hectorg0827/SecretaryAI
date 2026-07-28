/**
 * Centralized runtime configuration for the desktop app.
 *
 * The API URL comes from VITE_API_URL (baked in at build time) or an injected
 * window global. A production build is BLOCKED at compile time if the URL is
 * missing, uses an insecure scheme, or points at localhost/a placeholder — see
 * vite.config.ts. This module only provides a dev-mode convenience fallback.
 */

function resolveApiUrl(): string {
  const injected =
    typeof window !== 'undefined' && (window as any).__SECRETARY_API__
      ? String((window as any).__SECRETARY_API__)
      : '';
  const fromEnv = (import.meta.env.VITE_API_URL as string | undefined) ?? '';
  const url = injected || fromEnv;
  if (url) return url.replace(/\/+$/, ''); // trim trailing slashes

  // Dev-only fallback. In production the build guard guarantees a real URL,
  // so this branch is unreachable in a shipped bundle.
  if (import.meta.env.DEV) return 'http://localhost:8000';
  return '';
}

export const API_URL = resolveApiUrl();
