import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';

/** Invitation bearer token stays in URL fragment, not in the HTTP request URL. */
export function AcceptInvite() {
  const [token, setToken] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    setToken(fragment.get('token') ?? '');
    // Remove bearer token immediately from copyable URLs, history and screenshots.
    if (window.location.hash) window.history.replaceState({}, '', '/accept-invite');
  }, []);

  const bytes = new TextEncoder().encode(password).length;
  const validPassword = password.length >= 8 && bytes <= 72 && !password.includes('\0')
    && /[A-Za-z]/.test(password) && /[0-9]/.test(password);

  const accept = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!token || !validPassword || password !== confirmation) return;
    setError('');
    setLoading(true);
    try {
      await api.post('/api/team/invitations/accept', { token, password });
      setToken('');
      setPassword('');
      setConfirmation('');
      setDone(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not accept this invitation');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-900 flex items-center justify-center p-4">
      <div className="max-w-md w-full bg-white rounded-xl shadow-xl p-8 space-y-5">
        <h1 className="text-xl font-bold text-slate-900">Join Lion BI (staging)</h1>
        <p className="text-sm text-slate-600">Meet Lola, your AI business assistant at Lion Business Intelligence.</p>
        {done ? (
          <div role="status" className="space-y-4">
            <p className="text-emerald-700">Your Manager account is ready. Sign in with your email and new password.</p>
            <Link className="text-blue-700 underline" to="/login">Go to sign in</Link>
          </div>
        ) : !token ? (
          <p role="alert" className="text-amber-700">Your invitation link is missing. Ask your company owner to send a new invitation.</p>
        ) : (
          <form className="space-y-4" onSubmit={accept}>
            <p className="text-sm text-slate-600">Choose your own password. Your company owner will not see it.</p>
            {error && <p role="alert" className="text-red-700">{error}</p>}
            <label className="block text-sm font-medium text-slate-700" htmlFor="invite-password">New password</label>
            <input id="invite-password" type="password" autoComplete="new-password" required
              className="w-full rounded-lg border border-slate-300 px-3 py-2" value={password}
              onChange={(e) => setPassword(e.target.value)} />
            <p className="text-xs text-slate-500">At least 8 characters, one letter and number, at most 72 UTF-8 bytes.</p>
            <label className="block text-sm font-medium text-slate-700" htmlFor="invite-confirm">Confirm password</label>
            <input id="invite-confirm" type="password" autoComplete="new-password" required
              className="w-full rounded-lg border border-slate-300 px-3 py-2" value={confirmation}
              onChange={(e) => setConfirmation(e.target.value)} />
            <button type="submit" disabled={!validPassword || password !== confirmation || loading}
              className="w-full rounded-lg bg-blue-600 disabled:opacity-50 px-4 py-2 text-white font-medium">
              {loading ? 'Activating…' : 'Activate Manager account'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
