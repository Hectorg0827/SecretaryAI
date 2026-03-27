import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Zap, Loader2, AlertCircle, CheckCircle2 } from 'lucide-react';
import { api } from '../lib/api';

export function Register() {
  const navigate = useNavigate();

  const [companyName, setCompanyName] = useState('');
  const [fullName,    setFullName]    = useState('');
  const [email,       setEmail]       = useState('');
  const [password,    setPassword]    = useState('');
  const [confirm,     setConfirm]     = useState('');
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState<string | null>(null);

  const valid =
    companyName.trim().length > 0 &&
    fullName.trim().length > 0 &&
    email.includes('@') &&
    password.length >= 8 &&
    password === confirm;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    if (password !== confirm) { setError('Passwords do not match'); return; }

    setLoading(true);
    setError(null);

    try {
      const data = await api.post<{ access_token: string; company_id: string; role: string }>(
        '/auth/register',
        { email: email.trim().toLowerCase(), password, company_name: companyName.trim(), full_name: fullName.trim() },
      );
      localStorage.setItem('secretary_token',     data.access_token);
      localStorage.setItem('secretary_company_id', data.company_id);
      localStorage.setItem('secretary_role',       data.role);
      navigate('/', { replace: true });
    } catch (err: any) {
      setError(err.message ?? 'Registration failed. Please try again.');
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
          <h1 className="text-xl font-bold text-slate-900 mb-1">Create your account</h1>
          <p className="text-sm text-slate-400 mb-6">Set up your company workspace in seconds.</p>

          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <div className="flex items-center gap-2.5 bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2.5 text-sm">
                <AlertCircle size={14} className="flex-shrink-0" />
                {error}
              </div>
            )}

            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1.5" htmlFor="company">
                Company name
              </label>
              <input
                id="company"
                type="text"
                required
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                placeholder="Acme Distributors"
                className="w-full px-3.5 py-2.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent placeholder-slate-300 bg-white"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1.5" htmlFor="fullname">
                Your full name
              </label>
              <input
                id="fullname"
                type="text"
                required
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Jane Smith"
                className="w-full px-3.5 py-2.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent placeholder-slate-300 bg-white"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1.5" htmlFor="email">
                Work email
              </label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="jane@acme.com"
                className="w-full px-3.5 py-2.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent placeholder-slate-300 bg-white"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1.5" htmlFor="password">
                Password <span className="text-slate-400 font-normal">(min. 8 characters)</span>
              </label>
              <input
                id="password"
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full px-3.5 py-2.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent placeholder-slate-300 bg-white"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1.5" htmlFor="confirm">
                Confirm password
              </label>
              <div className="relative">
                <input
                  id="confirm"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  placeholder="••••••••"
                  className="w-full px-3.5 py-2.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent placeholder-slate-300 bg-white"
                />
                {confirm && password === confirm && (
                  <CheckCircle2 size={15} className="absolute right-3 top-1/2 -translate-y-1/2 text-emerald-500" />
                )}
              </div>
            </div>

            <button
              type="submit"
              disabled={loading || !valid}
              className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2.5 px-4 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-sm mt-2"
            >
              {loading && <Loader2 size={15} className="animate-spin" />}
              {loading ? 'Creating account…' : 'Create account'}
            </button>
          </form>

          <p className="text-xs text-slate-400 text-center mt-6">
            Already have an account?{' '}
            <Link to="/login" className="text-blue-600 hover:underline font-medium">
              Sign in
            </Link>
          </p>
        </div>

        <p className="text-xs text-slate-600 text-center mt-6">
          SecretaryAI · AI Operations Manager
        </p>
      </div>
    </div>
  );
}
