import React, { useState } from 'react';
import { useNavigate, useLocation, Link } from 'react-router-dom';
import { Loader2, AlertCircle, ArrowRight, ShieldCheck } from 'lucide-react';
import { LolaOrb } from '../components/lola/LolaOrb';
import { LolaGlyph } from '../components/lola/LolaGlyph';
import { LionBrand } from '../components/lola/LionBrand';
import { api } from '../lib/api';

export function Login() {
  const navigate  = useNavigate();
  const location  = useLocation();
  const requestedFrom = (location.state as { from?: { pathname?: unknown } } | null)?.from?.pathname;
  const from = typeof requestedFrom === 'string' && requestedFrom.startsWith('/') && !requestedFrom.startsWith('//')
    ? requestedFrom : '/';

  const [email,      setEmail]      = useState('');
  const [password,   setPassword]   = useState('');
  const [totpCode,   setTotpCode]   = useState('');
  const [needs2fa,   setNeeds2fa]   = useState(false);
  const [preAuthToken, setPreAuthToken] = useState<string | null>(null);
  const [loading,    setLoading]    = useState(false);
  const [error,      setError]      = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!needs2fa && (!email || !password)) return;
    if (needs2fa && (!preAuthToken || totpCode.length !== 6)) return;

    setLoading(true);
    setError(null);

    try {
      const data = needs2fa && preAuthToken
        ? await api.auth.verify2fa(preAuthToken, totpCode)
        : await api.auth.login(email.trim().toLowerCase(), password);

      if ('requires_2fa' in data && data.requires_2fa) {
        if (!data.pre_auth_token) throw new Error('Unable to establish the two-factor session. Please sign in again.');
        setPreAuthToken(data.pre_auth_token);
        setPassword('');
        setNeeds2fa(true);
        return;
      }

      const full = data as { access_token: string; company_id: string; role: string };
      if (!full.access_token || !full.company_id || !full.role) throw new Error('Sign-in response was incomplete.');
      setPreAuthToken(null);
      setTotpCode('');
      setPassword('');
      localStorage.setItem('secretary_token', full.access_token);
      localStorage.setItem('secretary_company_id', full.company_id);
      localStorage.setItem('secretary_role', full.role);
      navigate(from, { replace: true });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Sign-in could not be completed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative min-h-dvh overflow-y-auto bg-[#080e1c] text-[#f3f0f9]">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0"
        style={{ background: 'radial-gradient(ellipse at 20% 30%,rgba(102,79,166,.23),transparent 52%),radial-gradient(ellipse at 80% 80%,rgba(220,178,103,.10),transparent 44%)' }} />
      <header className="relative z-10 mx-auto flex max-w-6xl items-center justify-between px-5 py-7 md:px-10">
        <LionBrand />
        <span className="inline-flex items-center gap-2 rounded-full border border-[#3a4862] bg-[#131c30] px-3 py-1.5 text-[11px] text-[#d3c6a9]">
          <ShieldCheck size={13} /> Private business intelligence
        </span>
      </header>
      <div className="relative z-10 mx-auto grid max-w-6xl gap-10 px-5 pb-16 pt-4 md:min-h-[75vh] md:grid-cols-[1fr_460px] md:items-center md:px-10 md:pt-0">
        <section className="hidden max-w-lg flex-col items-start gap-6 md:flex" aria-label="Lola business assistant">
          <div className="w-36"><LolaOrb /></div>
          <div className="inline-flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.25em] text-[#e5c38b]"><LolaGlyph size={16} /> Meet Lola</div>
          <h1 className="text-5xl font-semibold leading-[1.08] tracking-[-0.045em] text-white">
            Your business.<br /><span className="text-[#dfc08c]">One conversation.</span>
          </h1>
          <p className="max-w-md text-base leading-7 text-[#aebbd2]">
            Finance, sales, operations and decisions — brought together in your authorized Lion BI workspace.
          </p>
        </section>
        <section className="rounded-[28px] border border-[#3c445e] bg-[#121b2f]/95 p-6 shadow-[0_26px_90px_rgba(0,0,0,.4)] sm:p-9" aria-label="Sign in to Lion BI">
          <div className="mb-8">
            <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.22em] text-[#e4c18c]">LION BUSINESS INTELLIGENCE</p>
            <h1 className="text-2xl font-semibold tracking-[-0.03em] text-white md:text-3xl">
              {needs2fa ? 'Verify your identity' : 'Welcome back'}
            </h1>
            <p className="mt-2 text-sm leading-6 text-[#aab8d0]">
              {needs2fa ? 'Enter the code from your authenticator app to continue securely.' : 'Sign in to continue with Lola and your business workspace.'}
            </p>
          </div>
          <form onSubmit={handleSubmit} className="space-y-5">
            {error && (
              <div role="alert" className="flex items-start gap-2.5 rounded-xl border border-[#8d5254] bg-[#3b252f] px-3 py-3 text-sm text-[#ffd6d3]">
                <AlertCircle size={17} className="mt-0.5 flex-shrink-0" /> {error}
              </div>
            )}
            {!needs2fa ? (
              <>
                <div>
                  <label className="mb-2 block text-xs font-semibold text-[#c4cee1]" htmlFor="email">Email address</label>
                  <input id="email" type="email" autoComplete="email" required value={email}
                    onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com"
                    className="w-full rounded-xl border border-[#46536f] bg-[#1b2640] px-4 py-3 text-sm text-[#f8f7ff] outline-none placeholder:text-[#92a1bb] focus:border-[#e3c18d] focus:ring-2 focus:ring-[#e3c18d]/25" />
                </div>
                <div>
                  <label className="mb-2 block text-xs font-semibold text-[#c4cee1]" htmlFor="password">Password</label>
                  <input id="password" type="password" autoComplete="current-password" required value={password}
                    onChange={(event) => setPassword(event.target.value)} placeholder="Enter your password"
                    className="w-full rounded-xl border border-[#46536f] bg-[#1b2640] px-4 py-3 text-sm text-[#f8f7ff] outline-none placeholder:text-[#92a1bb] focus:border-[#e3c18d] focus:ring-2 focus:ring-[#e3c18d]/25" />
                </div>
              </>
            ) : (
              <div>
                <label className="mb-2 block text-xs font-semibold text-[#c4cee1]" htmlFor="totp">Authenticator code</label>
                <input id="totp" type="text" inputMode="numeric" autoComplete="one-time-code" required maxLength={6}
                  value={totpCode} onChange={(event) => setTotpCode(event.target.value.replace(/\D/g, ''))}
                  placeholder="000000" autoFocus
                  className="w-full rounded-xl border border-[#46536f] bg-[#1b2640] px-4 py-3 text-center font-mono text-lg tracking-[0.3em] text-[#f8f7ff] outline-none placeholder:text-[#92a1bb] focus:border-[#e3c18d] focus:ring-2 focus:ring-[#e3c18d]/25" />
              </div>
            )}
            <button type="submit" disabled={loading || (!needs2fa && (!email || !password)) || (needs2fa && totpCode.length !== 6)}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-[#e3c18d] px-4 py-3 text-sm font-semibold text-[#151b2a] transition hover:bg-[#f2d6a7] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#e3c18d] disabled:cursor-not-allowed disabled:opacity-50">
              {loading ? <Loader2 size={17} className="animate-spin" /> : <ArrowRight size={17} />}
              {loading ? 'Verifying…' : needs2fa ? 'Verify and continue' : 'Sign in to Lion BI'}
            </button>
            {needs2fa && (
              <button type="button" onClick={() => { setNeeds2fa(false); setPreAuthToken(null); setPassword(''); setTotpCode(''); setError(null); }}
                className="w-full py-1 text-xs text-[#b8c4da] hover:text-[#e3c18d]">Back to sign in</button>
            )}
          </form>
          {!needs2fa && (
            <div className="mt-6 space-y-3 border-t border-[#35415a] pt-6 text-center text-xs text-[#adb9cf]">
              <p><Link to="/forgot-password" className="font-medium text-[#e4c38f] hover:underline">Forgot your password?</Link></p>
              <p>New to Lion BI? <Link to="/register" className="font-medium text-[#e4c38f] hover:underline">Create an account</Link></p>
            </div>
          )}
          <p className="mt-7 flex items-center justify-center gap-1.5 text-center text-[11px] text-[#8e9bb4]">
            <ShieldCheck size={13} /> Authorized access only · Your permissions control what Lola can see
          </p>
        </section>
      </div>
    </div>
  );
}
