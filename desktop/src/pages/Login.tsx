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
    if (!needs2fa && (!email.trim() || !password)) {
      setError('Enter your email and password.');
      return;
    }
    if (needs2fa && !/^\d{6}$/.test(totp)) {
      setError('Enter the six-digit code from your authenticator app.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await login(email.trim().toLowerCase(), needs2fa ? '' : password, {
        totpCode: needs2fa ? totp : undefined,
        preAuthToken: preAuthToken ?? undefined,
      });
      if ('requires2fa' in result) {
        setPassword('');
        setTotp('');
        setNeeds2fa(true);
        setPreAuthToken(result.preAuthToken);
        return;
      }
      onSuccess();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Sign-in could not be completed.');
    } finally {
      setLoading(false);
    }
  }

  function cancel2fa() {
    setNeeds2fa(false);
    setPreAuthToken(null);
    setTotp('');
    setPassword('');
    setError(null);
  }

  return (
    <div style={s.container}>
      <form onSubmit={handleSubmit} style={s.card}>
        <div style={s.logo}>SecretaryAI</div>
        <div style={s.subtitle}>Sign in to your account</div>

        {error && <div style={s.error} role="alert">{error}</div>}

        {!needs2fa ? (
          <>
            <label htmlFor="desktop-login-email" style={s.label}>Email</label>
            <input
              id="desktop-login-email"
              style={s.input}
              type="email"
              autoComplete="username"
              value={email}
              autoFocus
              onChange={(e) => setEmail(e.target.value)}
            />
            <label htmlFor="desktop-login-password" style={s.label}>Password</label>
            <input
              id="desktop-login-password"
              style={s.input}
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </>
        ) : (
          <>
            <div style={s.subtitle}>Confirm sign-in for {email}</div>
            <label htmlFor="desktop-login-totp" style={s.label}>Authenticator code</label>
          </>
        )}
        {needs2fa && (
          <input
            id="desktop-login-totp"
            style={s.input}
            type="text"
            inputMode="numeric"
            placeholder="6-digit authentication code"
            autoComplete="one-time-code"
            pattern="[0-9]{6}"
            maxLength={6}
            value={totp}
            autoFocus
            onChange={(e) => setTotp(e.target.value.replace(/\D/g, '').slice(0, 6))}
            disabled={loading}
          />
        )}

        <button style={{ ...s.button, opacity: loading ? 0.6 : 1 }} type="submit" disabled={loading}>
          {loading ? 'Signing in…' : needs2fa ? 'Verify' : 'Sign in'}
        </button>
        {needs2fa && (
          <button style={s.cancel} type="button" onClick={cancel2fa} disabled={loading}>
            Back to sign in
          </button>
        )}
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
  label: { color: '#cbd5e1', fontSize: 13, fontWeight: 600 },
  input: {
    padding: '11px 14px', borderRadius: 10, border: '1px solid #334155',
    background: '#1e293b', color: '#e2e8f0', fontSize: 14, outline: 'none',
  },
  button: {
    marginTop: 4, padding: '11px 14px', borderRadius: 10, border: 'none',
    background: '#6366f1', color: '#fff', fontSize: 15, fontWeight: 600, cursor: 'pointer',
  },
  cancel: { background: 'transparent', border: 'none', color: '#cbd5e1', cursor: 'pointer', padding: 10 },
  error: {
    background: '#7f1d1d', color: '#fecaca', padding: '9px 12px', borderRadius: 8, fontSize: 13,
  },
};
