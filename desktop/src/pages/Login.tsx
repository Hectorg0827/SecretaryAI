import React, { useState } from 'react';
import { login } from '../auth';

/**
 * Native desktop login. Talks to the backend /auth/login directly and stores
 * the returned token in the OS credential vault (see ../auth). No remote web
 * page, no localStorage.
 */
export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [totp, setTotp] = useState('');
  const [needs2fa, setNeeds2fa] = useState(false);
  const [preAuthToken, setPreAuthToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email || !password) return;
    setLoading(true);
    setError(null);
    try {
      const result = await login(email.trim().toLowerCase(), password, {
        totpCode: needs2fa ? totp : undefined,
        preAuthToken: preAuthToken ?? undefined,
      });
      if ('requires2fa' in result) {
        setNeeds2fa(true);
        setPreAuthToken(result.preAuthToken);
        setLoading(false);
        return;
      }
      onSuccess();
    } catch (err: any) {
      setError(err?.message ?? 'Invalid credentials');
      setLoading(false);
    }
  }

  return (
    <div style={s.container}>
      <form onSubmit={handleSubmit} style={s.card}>
        <div style={s.logo}>SecretaryAI</div>
        <div style={s.subtitle}>Sign in to your account</div>

        {error && <div style={s.error}>{error}</div>}

        <input
          style={s.input}
          type="email"
          placeholder="Email"
          value={email}
          autoFocus
          onChange={(e) => setEmail(e.target.value)}
          disabled={needs2fa}
        />
        <input
          style={s.input}
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={needs2fa}
        />
        {needs2fa && (
          <input
            style={s.input}
            type="text"
            inputMode="numeric"
            placeholder="6-digit authentication code"
            value={totp}
            autoFocus
            onChange={(e) => setTotp(e.target.value)}
          />
        )}

        <button style={{ ...s.button, opacity: loading ? 0.6 : 1 }} type="submit" disabled={loading}>
          {loading ? 'Signing in…' : needs2fa ? 'Verify' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}

const s: Record<string, React.CSSProperties> = {
  container: {
    minHeight: '100vh',
    background: '#0f172a',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontFamily: "'Inter', system-ui, sans-serif",
    padding: 24,
  },
  card: { width: '100%', maxWidth: 340, display: 'flex', flexDirection: 'column', gap: 12 },
  logo: {
    fontSize: 22, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase',
    color: '#6366f1', textAlign: 'center',
  },
  subtitle: { color: '#94a3b8', fontSize: 14, textAlign: 'center', marginBottom: 8 },
  input: {
    padding: '11px 14px', borderRadius: 10, border: '1px solid #334155',
    background: '#1e293b', color: '#e2e8f0', fontSize: 14, outline: 'none',
  },
  button: {
    marginTop: 4, padding: '11px 14px', borderRadius: 10, border: 'none',
    background: '#6366f1', color: '#fff', fontSize: 15, fontWeight: 600, cursor: 'pointer',
  },
  error: {
    background: '#7f1d1d', color: '#fecaca', padding: '9px 12px', borderRadius: 8, fontSize: 13,
  },
};
