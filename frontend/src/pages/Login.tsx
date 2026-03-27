import React, { useState } from 'react';
import { useNavigate, useLocation, Link } from 'react-router-dom';
import { Zap, Loader2, AlertCircle } from 'lucide-react';
import { api } from '../lib/api';

export function Login() {
  const navigate  = useNavigate();
  const location  = useLocation();
  const from      = (location.state as any)?.from?.pathname ?? '/';

  const [email,      setEmail]      = useState('');
  const [password,   setPassword]   = useState('');
  const [totpCode,   setTotpCode]   = useState('');
  const [needs2fa,   setNeeds2fa]   = useState(false);
  const [loading,    setLoading]    = useState(false);
  const [error,      setError]      = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) return;

    setLoading(true);
    setError(null);

    try {
      const data = await api.auth.login(
        email.trim().toLowerCase(),
        password,
        needs2fa ? totpCode : undefined,
      );

      if ('requires_2fa' in data && data.requires_2fa) {
        setNeeds2fa(true);
        setLoading(false);
        return;
      }

      const full = data as { access_token: string; company_id: string; role: string };
      localStorage.setItem('secretary_token', full.access_token);
      localStorage.setItem('secretary_company_id', full.company_id);
      localStorage.setItem('secretary_role', full.role);
      navigate(from, { replace: true });
    } catch (err: any) {
      setError(err.message ?? 'Invalid credentials');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-900 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex items-center gap-2.5 justify-center mb-8">
          <div className="w-10 h-10 bg-blue-500 rounded-xl flex items-center justify-center shadow-lg">
            <Zap size={20} className="text-white" />
          </div>
          <span className="text-2xl font-bold text-white tracking-tight">SecretaryAI</span>
        </div>

        {/* Card */}
        <div className="bg-white rounded-2xl shadow-2xl p-8">
          <h1 className="text-xl font-bold text-slate-900 mb-1">
            {needs2fa ? 'Two-factor authentication' : 'Welcome back'}
          </h1>
          <p className="text-sm text-slate-400 mb-6">
            {needs2fa
              ? 'Enter the 6-digit code from your authenticator app.'
              : 'Sign in to your account'}
          </p>

          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <div className="flex items-center gap-2.5 bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2.5 text-sm">
                <AlertCircle size={14} className="flex-shrink-0" />
                {error}
              </div>
            )}

            {!needs2fa ? (
              <>
                <div>
                  <label className="block text-xs font-semibold text-slate-600 mb-1.5" htmlFor="email">
                    Email address
                  </label>
                  <input
                    id="email"
                    type="email"
                    autoComplete="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@company.com"
                    className="w-full px-3.5 py-2.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent placeholder-slate-300 bg-white"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-slate-600 mb-1.5" htmlFor="password">
                    Password
                  </label>
                  <input
                    id="password"
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    className="w-full px-3.5 py-2.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent placeholder-slate-300 bg-white"
                  />
                </div>
              </>
            ) : (
              <div>
                <label className="block text-xs font-semibold text-slate-600 mb-1.5" htmlFor="totp">
                  Authenticator code
                </label>
                <input
                  id="totp"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  required
                  maxLength={6}
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, ''))}
                  placeholder="000000"
                  className="w-full px-3.5 py-2.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent placeholder-slate-300 bg-white tracking-widest text-center text-lg font-mono"
                  autoFocus
                />
              </div>
            )}

            <button
              type="submit"
              disabled={loading || (!needs2fa && (!email || !password)) || (needs2fa && totpCode.length !== 6)}
              className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2.5 px-4 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-sm mt-2"
            >
              {loading && <Loader2 size={15} className="animate-spin" />}
              {loading ? 'Verifying…' : needs2fa ? 'Verify code' : 'Sign in'}
            </button>

            {needs2fa && (
              <button
                type="button"
                onClick={() => { setNeeds2fa(false); setTotpCode(''); setError(null); }}
                className="w-full text-xs text-slate-400 hover:text-slate-600 py-1"
              >
                ← Back to sign in
              </button>
            )}
          </form>

          {!needs2fa && (
            <p className="text-xs text-slate-400 text-center mt-6">
              Forgot your password? Contact your account owner.
            </p>
          )}
          {!needs2fa && (
            <p className="text-xs text-slate-400 text-center mt-3">
              New to SecretaryAI?{' '}
              <Link to="/register" className="text-blue-600 hover:underline font-medium">
                Create an account
              </Link>
            </p>
          )}
        </div>

        <p className="text-xs text-slate-600 text-center mt-6">
          SecretaryAI · AI Operations Manager
        </p>
      </div>
    </div>
  );
}
