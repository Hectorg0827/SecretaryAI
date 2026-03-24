/**
 * Desktop app root.
 *
 * On first render we check whether QuickBooks is already connected.
 * - Not connected → show the onboarding wizard (Setup page)
 * - Connected (or user skips) → show the main dashboard shell
 *
 * The main shell is a lightweight frame that embeds the SecretaryAI web app
 * in a Tauri webview, giving desktop users the full web UI without
 * duplicating all the frontend components.
 */
import React, { useEffect, useState } from 'react';
import Setup from './pages/Setup';

const API =
  typeof window !== 'undefined' && (window as any).__SECRETARY_API__
    ? (window as any).__SECRETARY_API__
    : import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

const APP_URL =
  typeof window !== 'undefined' && (window as any).__SECRETARY_APP_URL__
    ? (window as any).__SECRETARY_APP_URL__
    : import.meta.env.VITE_APP_URL ?? 'https://app.secretaryai.com';

type AppState = 'loading' | 'setup' | 'app';

export function App() {
  const [state, setState] = useState<AppState>('loading');

  useEffect(() => {
    checkConnection();
  }, []);

  async function checkConnection() {
    const token = localStorage.getItem('secretary_token');
    if (!token) {
      // No auth token — show setup (it handles its own auth redirect if needed)
      setState('setup');
      return;
    }
    try {
      const res = await fetch(`${API}/api/setup/qb-desktop/status`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setState(data.connected ? 'app' : 'setup');
      } else {
        // 401 or other error — go to setup which will redirect to login
        setState('setup');
      }
    } catch {
      // Network error — default to setup so user isn't stuck on a blank screen
      setState('setup');
    }
  }

  if (state === 'loading') {
    return (
      <div style={loadingStyles.container}>
        <div style={loadingStyles.logo}>SecretaryAI</div>
        <div style={loadingStyles.spinner} />
      </div>
    );
  }

  if (state === 'setup') {
    return <Setup onComplete={() => setState('app')} />;
  }

  // Main app — embed the web frontend in an iframe.
  // The Tauri CSP allows connecting to the API so the webview has full access.
  return (
    <iframe
      src={APP_URL}
      style={{ width: '100vw', height: '100vh', border: 'none', display: 'block' }}
      title="SecretaryAI"
    />
  );
}

const loadingStyles: Record<string, React.CSSProperties> = {
  container: {
    minHeight: '100vh',
    background: '#0f172a',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '20px',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  logo: {
    fontSize: '20px',
    fontWeight: 700,
    letterSpacing: '0.1em',
    textTransform: 'uppercase' as const,
    color: '#6366f1',
  },
  spinner: {
    width: '28px',
    height: '28px',
    border: '3px solid #1e293b',
    borderTop: '3px solid #6366f1',
    borderRadius: '50%',
    animation: 'spin 1s linear infinite',
  },
};
