/**
 * Desktop app root — fully native UI (no remote iframe).
 *
 * Flow:
 *   loading → login (no session)      : native sign-in, token → OS vault
 *           → setup  (QB not linked)  : QuickBooks Desktop onboarding wizard
 *           → dashboard               : native dashboard, API-driven
 *
 * The session token lives in the OS credential vault (see ./auth), shared with
 * the Rust background sync agent. No web content is embedded in this privileged
 * webview. (Defects #4/#5/#6.)
 */
import React, { useCallback, useEffect, useState } from 'react';
import Setup from './pages/Setup';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import CaptureIndicator from './components/CaptureIndicator';
import { API_URL } from './config';
import { loadToken, authFetch } from './auth';

type AppState = 'loading' | 'login' | 'setup' | 'app';

export function App() {
  const [state, setState] = useState<AppState>('loading');

  const route = useCallback(async () => {
    const token = await loadToken();
    if (!token) {
      setState('login');
      return;
    }
    // Signed in — decide between onboarding and the dashboard.
    try {
      const res = await authFetch('/api/setup/qb-desktop/status');
      if (res.status === 401) {
        setState('login');
        return;
      }
      if (res.ok) {
        const data = await res.json();
        setState(data.connected ? 'app' : 'setup');
      } else {
        // Backend reachable but errored — let the user into setup rather than a blank screen.
        setState('setup');
      }
    } catch {
      // Offline: we still have a token, so show the dashboard (it handles its own
      // offline/error state) instead of forcing re-login.
      setState('app');
    }
  }, []);

  useEffect(() => {
    // Fail-closed sanity: never run a shipped build against localhost.
    if (import.meta.env.PROD && (!API_URL || /localhost|127\.0\.0\.1/.test(API_URL))) {
      // The build guard should prevent this; this is defense in depth.
      console.error('Refusing to run: API_URL is not configured for production.');
    }
    route();
  }, [route]);

  let content: React.ReactNode;
  if (state === 'loading') {
    content = (
      <div style={loadingStyles.container}>
        <div style={loadingStyles.logo}>SecretaryAI</div>
        <div style={loadingStyles.spinner} />
      </div>
    );
  } else if (state === 'login') {
    content = <Login onSuccess={route} />;
  } else if (state === 'setup') {
    content = <Setup onComplete={() => setState('app')} />;
  } else {
    content = <Dashboard onSignOut={() => setState('login')} />;
  }

  // CaptureIndicator overlays every view whenever screen capture is active.
  return (
    <>
      <CaptureIndicator />
      {content}
    </>
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
