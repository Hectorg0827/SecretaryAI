import React, { useEffect, useState } from 'react';
import { open } from '@tauri-apps/plugin-shell';
import { authFetch, clearToken } from '../auth';

const APP_URL =
  (typeof window !== 'undefined' && (window as any).__SECRETARY_APP_URL__) ||
  (import.meta.env.VITE_APP_URL as string | undefined) ||
  'https://app.secretaryai.com';

type State = 'loading' | 'ready' | 'error';

/**
 * Native dashboard shell. Fetches the real dashboard summary from the backend
 * (authenticated via the OS-vault token) and renders it natively — no remote
 * iframe in the privileged webview. The full web UI is reachable via the
 * external browser (secure: not a Tauri webview).
 */
export default function Dashboard({ onSignOut }: { onSignOut: () => void }) {
  const [state, setState] = useState<State>('loading');
  const [summary, setSummary] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setState('loading');
    setError(null);
    try {
      const res = await authFetch('/api/dashboard/summary');
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      setSummary(await res.json());
      setState('ready');
    } catch (e: any) {
      setError(e?.message ?? 'Could not reach the backend');
      setState('error');
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function signOut() {
    await clearToken();
    onSignOut();
  }

  // Render any scalar top-level metrics the summary exposes, defensively.
  const metrics = summary
    ? Object.entries(summary).filter(
        ([, v]) => typeof v === 'number' || typeof v === 'string',
      )
    : [];

  return (
    <div style={s.page}>
      <header style={s.header}>
        <span style={s.logo}>SecretaryAI</span>
        <div style={{ display: 'flex', gap: 10 }}>
          <button style={s.ghost} onClick={() => open(APP_URL)}>Open full app ↗</button>
          <button style={s.ghost} onClick={load}>Refresh</button>
          <button style={s.ghost} onClick={signOut}>Sign out</button>
        </div>
      </header>

      <main style={s.main}>
        {state === 'loading' && <div style={s.muted}>Loading your dashboard…</div>}

        {state === 'error' && (
          <div style={s.errorBox}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>Can't load data right now</div>
            <div style={{ fontSize: 13, color: '#fecaca' }}>{error}</div>
            <button style={{ ...s.ghost, marginTop: 12 }} onClick={load}>Try again</button>
          </div>
        )}

        {state === 'ready' && (
          <>
            {metrics.length > 0 ? (
              <div style={s.grid}>
                {metrics.map(([k, v]) => (
                  <div key={k} style={s.tile}>
                    <div style={s.tileValue}>{String(v)}</div>
                    <div style={s.tileLabel}>{k.replace(/_/g, ' ')}</div>
                  </div>
                ))}
              </div>
            ) : (
              <div style={s.muted}>
                Connected. Open the full app for accounts, inventory, inbox, and chat.
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}

const s: Record<string, React.CSSProperties> = {
  page: { minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: "'Inter', system-ui, sans-serif" },
  header: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '14px 20px', borderBottom: '1px solid #1e293b',
  },
  logo: { fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: '#6366f1' },
  ghost: {
    background: 'transparent', border: '1px solid #334155', color: '#cbd5e1',
    padding: '7px 12px', borderRadius: 8, fontSize: 13, cursor: 'pointer',
  },
  main: { padding: 24 },
  muted: { color: '#94a3b8', fontSize: 14 },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 14 },
  tile: { background: '#1e293b', border: '1px solid #334155', borderRadius: 12, padding: 16 },
  tileValue: { fontSize: 24, fontWeight: 700, color: '#f1f5f9' },
  tileLabel: { fontSize: 12, color: '#94a3b8', marginTop: 4, textTransform: 'capitalize' },
  errorBox: { background: '#1e293b', border: '1px solid #7f1d1d', borderRadius: 12, padding: 20, maxWidth: 460 },
};
