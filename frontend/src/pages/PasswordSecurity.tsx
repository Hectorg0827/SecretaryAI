import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';

function RecoveryShell({ title, children }: { title: string; children: ReactNode }) {
  return <main className="min-h-screen bg-slate-900 flex items-center justify-center p-4">
    <section className="w-full max-w-md rounded-2xl bg-white p-7 shadow-xl space-y-5">
      <h1 className="text-2xl font-bold">{title}</h1>{children}
      <Link to="/login" className="block text-sm text-blue-700">Back to sign in</Link>
    </section>
  </main>;
}

export function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      await api.post('/auth/forgot-password', { email: email.trim().toLowerCase() });
      setDone(true);
    } catch (error) { setError(error instanceof Error ? error.message : 'Password recovery unavailable'); }
    finally { setBusy(false); }
  };
  return <RecoveryShell title="Reset your Lion BI password">
    <p className="text-sm text-slate-600">Staging recovery emails are limited to the approved test mailbox.</p>
    {done ? <p role="status" className="text-emerald-700 text-sm">If that email has an account, a reset link is on its way.</p>
      : <form onSubmit={submit} className="space-y-3">
        <label htmlFor="recovery-email" className="block text-sm font-medium">Email</label>
        <input id="recovery-email" type="email" required maxLength={254} autoComplete="email"
          className="w-full border rounded-lg p-2" value={email} onChange={(e) => setEmail(e.target.value)} />
        <button disabled={busy} className="w-full bg-blue-600 rounded-lg text-white p-2 disabled:opacity-50">{busy ? 'Sending…' : 'Send reset link'}</button>
      </form>}
    {error && <p role="alert" className="text-red-700 text-sm">{error}</p>}
  </RecoveryShell>;
}

export function ResetPassword() {
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get('token') || '');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  useEffect(() => { window.history.replaceState(window.history.state, '', `${window.location.pathname}${window.location.search}`); }, []);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (password !== confirm) { setError('Passwords do not match'); return; }
    setBusy(true); setError('');
    try {
      await api.post('/auth/reset-password', { token, new_password: password });
      setDone(true); setPassword(''); setConfirm('');
    } catch (error) { setError(error instanceof Error ? error.message : 'Reset link invalid or expired'); }
    finally { setBusy(false); }
  };
  return <RecoveryShell title="Choose a new password">
    {!token && <p role="alert" className="text-red-700 text-sm">No reset token. <Link to="/forgot-password" className="underline">Request a link</Link>.</p>}
    {done ? <p role="status" className="text-emerald-700 text-sm">Password changed. Please sign in again.</p>
      : token && <form onSubmit={submit} className="space-y-3">
        <p className="text-sm text-slate-600">Use 8+ characters, a letter and a number (72 UTF-8 bytes maximum).</p>
        <label htmlFor="reset-password" className="block text-sm font-medium">New password</label>
        <input id="reset-password" type="password" autoComplete="new-password" required minLength={8}
          className="w-full border rounded-lg p-2" value={password} onChange={(e) => setPassword(e.target.value)} />
        <label htmlFor="reset-confirm" className="block text-sm font-medium">Confirm password</label>
        <input id="reset-confirm" type="password" autoComplete="new-password" required minLength={8}
          className="w-full border rounded-lg p-2" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        <button disabled={busy} className="w-full bg-blue-600 text-white rounded-lg p-2 disabled:opacity-50">{busy ? 'Saving…' : 'Change password'}</button>
      </form>}
    {error && <p role="alert" className="text-red-700 text-sm">{error}</p>}
  </RecoveryShell>;
}

export function ChangePassword() {
  const [current, setCurrent] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (password !== confirm) { setError('Passwords do not match'); return; }
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await api.post<{ status: string; access_token: string }>('/auth/change-password', {
        current_password: current, new_password: password,
      });
      if (result.status !== 'ok' || !result.access_token) throw new Error('Could not confirm the new session');
      localStorage.setItem('secretary_token', result.access_token);
      setNotice('Password changed. Other devices must sign in again.');
      setCurrent(''); setPassword(''); setConfirm('');
    } catch (error) { setError(error instanceof Error ? error.message : 'Password change failed'); }
    finally { setBusy(false); }
  };
  return <section className="max-w-xl rounded-xl border border-slate-200 bg-white p-6 space-y-4">
    <h1 className="text-xl font-semibold">Account password</h1>
    <p className="text-sm text-slate-600">Changing your password ends other sessions. Use 8+ characters, a letter and a number.</p>
    <form onSubmit={submit} className="space-y-3">
      <label htmlFor="current-password" className="block text-sm font-medium">Current password</label>
      <input id="current-password" type="password" required autoComplete="current-password"
        className="w-full border rounded-lg p-2" value={current} onChange={(e) => setCurrent(e.target.value)} />
      <label htmlFor="change-password" className="block text-sm font-medium">New password</label>
      <input id="change-password" type="password" required minLength={8} autoComplete="new-password"
        className="w-full border rounded-lg p-2" value={password} onChange={(e) => setPassword(e.target.value)} />
      <label htmlFor="change-confirm" className="block text-sm font-medium">Confirm password</label>
      <input id="change-confirm" type="password" required minLength={8} autoComplete="new-password"
        className="w-full border rounded-lg p-2" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      <button disabled={busy} className="rounded-lg bg-blue-600 text-white px-4 py-2 disabled:opacity-50">{busy ? 'Updating…' : 'Change password'}</button>
    </form>
    {error && <p role="alert" className="text-red-700 text-sm">{error}</p>}
    {notice && <p role="status" className="text-emerald-700 text-sm">{notice}</p>}
  </section>;
}
