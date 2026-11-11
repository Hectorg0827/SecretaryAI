/**
 * Public account pages reached from an emailed link or the sign-in page:
 *   /forgot-password   ask for a reset link
 *   /reset-password    choose a new password (link from email)
 *   /accept-invite     an invited team member chooses a password
 *   /verify-email      confirm an email address
 *
 * The token from the link is read from the URL and then removed from the
 * address bar, so it is not left in browser history or shared by accident.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Zap, Loader2, AlertCircle, CheckCircle2 } from 'lucide-react';
import { api } from '../lib/api';
import { clearSession, saveSession } from '../lib/session';

const inputClass =
  'w-full px-3.5 py-2.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent placeholder-slate-300 bg-white';
const buttonClass =
  'w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2.5 px-4 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-sm mt-2';

function Shell({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-900 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-2.5 justify-center mb-8">
          <div className="w-10 h-10 bg-blue-500 rounded-xl flex items-center justify-center shadow-lg">
            <Zap size={20} className="text-white" />
          </div>
          <span className="text-2xl font-bold text-white tracking-tight">SecretaryAI</span>
        </div>
        <div className="bg-white rounded-2xl shadow-2xl p-8">
          <h1 className="text-xl font-bold text-slate-900 mb-1">{title}</h1>
          <p className="text-sm text-slate-400 mb-6">{subtitle}</p>
          {children}
          <p className="text-xs text-slate-400 text-center mt-6">
            <Link to="/login" className="text-blue-600 hover:underline font-medium">Back to sign in</Link>
          </p>
        </div>
      </div>
    </div>
  );
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div role="alert" className="flex items-center gap-2.5 bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2.5 text-sm">
      <AlertCircle size={14} className="flex-shrink-0" />
      {message}
    </div>
  );
}

function SuccessBox({ message }: { message: string }) {
  return (
    <div role="status" className="flex items-center gap-2.5 bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-lg px-3 py-2.5 text-sm">
      <CheckCircle2 size={14} className="flex-shrink-0" />
      {message}
    </div>
  );
}

/** Read ?token= once, then scrub it from the address bar. */
function useLinkToken(): string {
  const ref = useRef<string | null>(null);
  if (ref.current === null) {
    ref.current = new URLSearchParams(window.location.search).get('token') ?? '';
  }
  useEffect(() => {
    if (ref.current) window.history.replaceState({}, '', window.location.pathname);
  }, []);
  return ref.current;
}

/** Same rules the server enforces, so the user hears about it before submitting. */
export function passwordProblem(password: string, confirm: string): string | null {
  if (password.length < 8) return 'Use at least 8 characters.';
  if (!/[A-Za-z]/.test(password)) return 'Include at least one letter.';
  if (!/[0-9]/.test(password)) return 'Include at least one number.';
  if (password !== confirm) return 'The two passwords do not match.';
  return null;
}

function PasswordFields({ password, confirm, onPassword, onConfirm }: {
  password: string; confirm: string; onPassword: (v: string) => void; onConfirm: (v: string) => void;
}) {
  return (
    <>
      <div>
        <label className="block text-xs font-semibold text-slate-600 mb-1.5" htmlFor="new-password">New password</label>
        <input id="new-password" type="password" autoComplete="new-password" required value={password}
          onChange={(e) => onPassword(e.target.value)} className={inputClass} />
        <p className="text-xs text-slate-400 mt-1">At least 8 characters, with a letter and a number.</p>
      </div>
      <div>
        <label className="block text-xs font-semibold text-slate-600 mb-1.5" htmlFor="confirm-password">Type it again</label>
        <input id="confirm-password" type="password" autoComplete="new-password" required value={confirm}
          onChange={(e) => onConfirm(e.target.value)} className={inputClass} />
      </div>
    </>
  );
}

export function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true); setError(null);
    try {
      await api.auth.forgotPassword(email.trim().toLowerCase());
      setSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Please try again.');
    } finally { setLoading(false); }
  };

  return (
    <Shell title="Forgot your password?" subtitle="Enter your email and we will send you a link to choose a new one.">
      {sent ? (
        <SuccessBox message="If that email has an account, a reset link is on its way. It works for 30 minutes." />
      ) : (
        <form onSubmit={submit} className="space-y-4">
          {error && <ErrorBox message={error} />}
          <div>
            <label className="block text-xs font-semibold text-slate-600 mb-1.5" htmlFor="email">Email address</label>
            <input id="email" type="email" autoComplete="email" required value={email}
              onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" className={inputClass} />
          </div>
          <button type="submit" disabled={loading || !email.includes('@')} className={buttonClass}>
            {loading && <Loader2 size={15} className="animate-spin" />}
            Send reset link
          </button>
        </form>
      )}
    </Shell>
  );
}

export function ResetPassword() {
  const token = useLinkToken();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const problem = passwordProblem(password, confirm);
    if (problem) { setError(problem); return; }
    setLoading(true); setError(null);
    try {
      await api.auth.resetPassword(token, password);
      // Any session stored in this browser was just ended by the server.
      clearSession();
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Please try again.');
    } finally { setLoading(false); }
  };

  if (!token) {
    return (
      <Shell title="Reset your password" subtitle="">
        <ErrorBox message="This link is missing its code. Open the link from your email again, or ask for a new one." />
        <p className="text-xs text-center mt-4"><Link to="/forgot-password" className="text-blue-600 hover:underline">Send me a new link</Link></p>
      </Shell>
    );
  }

  return (
    <Shell title="Choose a new password" subtitle="You will be signed out everywhere else.">
      {done ? (
        <SuccessBox message="Your password has been changed. You can sign in now." />
      ) : (
        <form onSubmit={submit} className="space-y-4">
          {error && <ErrorBox message={error} />}
          <PasswordFields password={password} confirm={confirm} onPassword={setPassword} onConfirm={setConfirm} />
          <button type="submit" disabled={loading || !password || !confirm} className={buttonClass}>
            {loading && <Loader2 size={15} className="animate-spin" />}
            Change password
          </button>
        </form>
      )}
    </Shell>
  );
}

export function AcceptInvite() {
  const token = useLinkToken();
  const navigate = useNavigate();
  const [fullName, setFullName] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const problem = passwordProblem(password, confirm);
    if (problem) { setError(problem); return; }
    setLoading(true); setError(null);
    try {
      const session = await api.auth.acceptInvite(token, password, fullName.trim());
      saveSession(session);
      navigate('/', { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Please try again.');
    } finally { setLoading(false); }
  };

  if (!token) {
    return (
      <Shell title="Join your team" subtitle="">
        <ErrorBox message="This invitation link is missing its code. Open the link from your email again, or ask your account owner to send a new one." />
      </Shell>
    );
  }

  return (
    <Shell title="Join your team" subtitle="Choose a password to finish setting up your account.">
      <form onSubmit={submit} className="space-y-4">
        {error && <ErrorBox message={error} />}
        <div>
          <label className="block text-xs font-semibold text-slate-600 mb-1.5" htmlFor="full-name">Your name</label>
          <input id="full-name" type="text" autoComplete="name" value={fullName}
            onChange={(e) => setFullName(e.target.value)} className={inputClass} />
        </div>
        <PasswordFields password={password} confirm={confirm} onPassword={setPassword} onConfirm={setConfirm} />
        <button type="submit" disabled={loading || !password || !confirm} className={buttonClass}>
          {loading && <Loader2 size={15} className="animate-spin" />}
          Create my account
        </button>
      </form>
    </Shell>
  );
}

export function VerifyEmail() {
  const token = useLinkToken();
  const [state, setState] = useState<'working' | 'ok' | 'error'>(token ? 'working' : 'error');
  const [message, setMessage] = useState(token ? '' : 'This link is missing its code. Open the link from your email again.');
  const started = useRef(false);

  useEffect(() => {
    if (!token || started.current) return;
    started.current = true; // the link works once; never submit it twice
    api.auth.verifyEmail(token)
      .then((r) => { setState('ok'); setMessage(r.message); })
      .catch((err) => { setState('error'); setMessage(err instanceof Error ? err.message : 'Please try again.'); });
  }, [token]);

  return (
    <Shell title="Confirm your email" subtitle="">
      {state === 'working' && <p className="flex items-center gap-2 text-sm text-slate-500"><Loader2 size={15} className="animate-spin" /> Confirming…</p>}
      {state === 'ok' && <SuccessBox message={message} />}
      {state === 'error' && <ErrorBox message={message} />}
    </Shell>
  );
}
