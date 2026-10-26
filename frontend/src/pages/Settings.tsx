import React, { useState, useEffect, useCallback, useRef } from 'react';
import clsx from 'clsx';
import {
  Settings as SettingsIcon, Database, ShieldCheck, CreditCard,
  Check, AlertTriangle, Loader2, X, ExternalLink, Copy,
} from 'lucide-react';
import { api, IntegrationsResponse } from '../lib/api';
import { BillingStatus } from '../components/BillingStatus';
import { useAuth } from '../hooks/useAuth';
import toast from 'react-hot-toast';

interface Section { id: string; label: string; icon: React.ElementType; }

const SECTIONS: Section[] = [
  { id: 'integrations', label: 'Integrations',    icon: Database     },
  { id: 'security',     label: 'Security',         icon: ShieldCheck  },
  { id: 'billing',      label: 'Billing',          icon: CreditCard   },
];

/** Browser redirects must be to the exact consent hosts frozen by the OAuth backend. */
export function isTrustedConsentUrl(rawUrl: string, expectedHost: string): boolean {
  try {
    const parsed = new URL(rawUrl);
    return parsed.protocol === 'https:' && parsed.hostname === expectedHost
      && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}

// ─── Two-Factor Authentication section ────────────────────────────────────────

function TwoFASection() {
  type Step = 'idle' | 'setup' | 'verify' | 'enabled';

  const [step,     setStep]     = useState<Step>('idle');
  const [secret,   setSecret]   = useState('');
  const [code,     setCode]     = useState('');
  const [loading,  setLoading]  = useState(false);
  const [enabled,  setEnabled]  = useState(false);
  const [disCode,  setDisCode]  = useState('');
  const [disOpen,  setDisOpen]  = useState(false);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState(false);

  const refreshEnrollment = useCallback(async () => {
    setStatusLoading(true);
    try {
      const result = await api.get<{ totp_enabled: boolean }>('/auth/me');
      if (typeof result.totp_enabled !== 'boolean') throw new Error('Missing enrollment state');
      setEnabled(result.totp_enabled);
      setStatusError(false);
      setStep(result.totp_enabled ? 'enabled' : 'idle');
    } catch {
      setStatusError(true);
    } finally {
      setStatusLoading(false);
    }
  }, []);

  useEffect(() => { refreshEnrollment(); }, [refreshEnrollment]);

  // Start 2FA setup — request a new TOTP secret from the backend
  const startSetup = async () => {
    if (statusLoading || statusError || enabled) return;
    setLoading(true);
    try {
      const res = await api.post<{ secret: string; otpauth_url: string }>('/auth/2fa/setup', {});
      setSecret(res.secret);
      setStep('setup');
    } catch {
      toast.error('Could not start 2FA setup. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  // Verify the TOTP code and activate 2FA
  const verifyCode = async () => {
    if (code.length !== 6) return;
    setLoading(true);
    try {
      await api.post('/auth/2fa/verify', { totp_code: code });
      setEnabled(true);
      setStatusError(false);
      setStep('enabled');
      setSecret('');
      setCode('');
      toast.success('Two-factor authentication enabled.');
    } catch {
      toast.error('Invalid code. Try again.');
    } finally {
      setLoading(false);
    }
  };

  // Disable 2FA after verifying identity
  const disable2FA = async () => {
    if (disCode.length !== 6) return;
    setLoading(true);
    try {
      await api.deleteWithBody('/auth/2fa/disable', { totp_code: disCode });
      setEnabled(false);
      setStatusError(false);
      setStep('idle');
      setDisCode('');
      setDisOpen(false);
      toast.success('Two-factor authentication disabled.');
    } catch {
      toast.error('Invalid code. Could not disable 2FA.');
    } finally {
      setLoading(false);
    }
  };

  const copySecret = () => {
    navigator.clipboard.writeText(secret);
    toast.success('Secret copied to clipboard');
  };

  return (
    <div className="space-y-0">
      <SettingRow label="Two-Factor Authentication" description="Require a time-based one-time code in addition to your password.">
        {statusLoading ? (
          <span role="status" className="text-xs text-slate-500">Checking MFA status…</span>
        ) : statusError ? (
          <button onClick={refreshEnrollment} type="button" className="text-xs text-amber-700 underline">
            MFA status unavailable — retry
          </button>
        ) : step === 'idle' && !enabled && (
          <button
            onClick={startSetup}
            disabled={loading}
            className="px-3 py-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors disabled:opacity-50 flex items-center gap-1.5"
          >
            {loading && <Loader2 size={11} className="animate-spin" />}
            Enable
          </button>
        )}
        {!statusLoading && !statusError && enabled && (
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1 text-xs text-emerald-600 font-medium">
              <Check size={12} /> Active
            </span>
            <button
              onClick={() => setDisOpen(true)}
              className="px-3 py-1.5 text-xs font-medium text-slate-600 border border-slate-200 hover:bg-slate-50 rounded-lg transition-colors"
            >
              Disable
            </button>
          </div>
        )}
      </SettingRow>

      {/* Step: show QR + secret */}
      {step === 'setup' && (
        <div className="py-5 space-y-4 border-b border-slate-100">
          <p className="text-sm text-slate-600">
            Add the setup key below to your authenticator app manually, then enter its 6-digit code to confirm.
          </p>

          {/* Do not send a TOTP enrollment URI or secret to an external QR service. */}
          <div className="flex items-center gap-4">
            <p className="text-xs text-slate-600 max-w-40">For your security, enter the setup key manually in your authenticator app. SecretaryAI never sends it to a QR-image provider.</p>
            <div className="min-w-0">
              <p className="text-xs text-slate-500 mb-1">Authenticator setup key:</p>
              <div className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
                <code className="text-xs font-mono text-slate-700 break-all">{secret}</code>
                <button onClick={copySecret} className="flex-shrink-0 text-slate-400 hover:text-slate-600">
                  <Copy size={13} />
                </button>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <input
              type="text"
              inputMode="numeric"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              placeholder="000000"
              className="w-32 px-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 font-mono text-center tracking-widest"
            />
            <button
              onClick={verifyCode}
              disabled={loading || code.length !== 6}
              className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-50 transition-colors flex items-center gap-1.5"
            >
              {loading && <Loader2 size={13} className="animate-spin" />}
              Verify & activate
            </button>
            <button onClick={() => { setStep('idle'); setSecret(''); }} className="text-xs text-slate-400 hover:text-slate-600 px-2">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Disable confirmation */}
      {disOpen && (
        <div className="py-5 border-b border-slate-100 space-y-3">
          <p className="text-sm text-slate-600">Enter the 6-digit code from your authenticator app to confirm.</p>
          <div className="flex items-center gap-2">
            <input
              type="text"
              inputMode="numeric"
              maxLength={6}
              value={disCode}
              onChange={(e) => setDisCode(e.target.value.replace(/\D/g, ''))}
              placeholder="000000"
              className="w-32 px-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-400 font-mono text-center tracking-widest"
              autoFocus
            />
            <button
              onClick={disable2FA}
              disabled={loading || disCode.length !== 6}
              className="px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-lg disabled:opacity-50 transition-colors flex items-center gap-1.5"
            >
              {loading && <Loader2 size={13} className="animate-spin" />}
              Disable 2FA
            </button>
            <button onClick={() => { setDisOpen(false); setDisCode(''); }} className="text-xs text-slate-400 hover:text-slate-600 px-2">
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function SettingRow({ label, description, children }: { label: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4 py-4 border-b border-slate-100 last:border-0">
      <div className="flex-1">
        <div className="text-sm font-medium text-slate-800">{label}</div>
        {description && <div className="text-xs text-slate-400 mt-0.5">{description}</div>}
      </div>
      {children}
    </div>
  );
}

// ─── QB Desktop setup modal (agentic) ─────────────────────────────────────────

type QBDStep = 'start' | 'waiting' | 'done' | 'error';

function QBDSetupModal({ onClose, onConnected }: { onClose: () => void; onConnected: () => void }) {
  const [step, setStep]           = useState<QBDStep>('start');
  const [starting, setStarting]   = useState(false);
  const [statusMsg, setStatusMsg] = useState('');
  const [errorMsg, setErrorMsg]   = useState('');
  const [authorizationUrl, setAuthorizationUrl] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const failuresRef = useRef(0);

  // Clean up polling when modal unmounts
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const startSetup = async () => {
    setStarting(true);
    try {
      const data = await api.setup.qbdStart();
      const url = new URL(data.auth_flow_url);
      if (url.protocol !== 'https:' || url.username || url.password) {
        throw new Error('Untrusted QuickBooks authorization URL');
      }
      setAuthorizationUrl(url.toString());
      setStep('waiting');
      setStatusMsg('Open the authorization link below, then follow the QuickBooks prompts.');
      beginPolling();
    } catch (e: any) {
      setErrorMsg(e.message ?? 'Could not start setup. Check that Conductor is configured.');
      setStep('error');
    } finally {
      setStarting(false);
    }
  };

  const beginPolling = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    failuresRef.current = 0;
    pollRef.current = setInterval(async () => {
      try {
        const status = await api.setup.qbdStatus();
        failuresRef.current = 0;
        setStatusMsg(status.message);
        if (status.status === 'error') {
          if (pollRef.current) clearInterval(pollRef.current);
          setErrorMsg(status.message);
          setStep('error');
          return;
        }
        if (status.connected) {
          if (pollRef.current) clearInterval(pollRef.current);
          setStep('done');
          onConnected();
        }
      } catch {
        failuresRef.current += 1;
        if (failuresRef.current >= 3) {
          if (pollRef.current) clearInterval(pollRef.current);
          setErrorMsg('QuickBooks connection status could not be verified after repeated attempts.');
          setStep('error');
        }
      }
    }, 5000);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="bg-white rounded-xl shadow-xl border border-slate-100 w-full max-w-md p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-base font-bold text-slate-900">Connect QuickBooks Desktop</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={16} /></button>
        </div>

        {step === 'start' && (
          <>
            <p className="text-sm text-slate-600 mb-4 leading-relaxed">
              Click <strong>Open Setup</strong> — a browser window will guide you through
              downloading a small file and authorizing access in QuickBooks.
              No technical knowledge needed.
            </p>
            <div className="bg-slate-50 rounded-lg p-3 mb-5 space-y-2">
              {[
                'A setup page opens in your browser',
                'Download the .QWC file when prompted',
                'Open it — QuickBooks will ask for permission',
                'Click "Yes, always allow access"',
              ].map((txt, i) => (
                <div key={i} className="flex items-start gap-2.5">
                  <span className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-600 text-white text-xs font-bold flex items-center justify-center mt-0.5">{i + 1}</span>
                  <span className="text-xs text-slate-600">{txt}</span>
                </div>
              ))}
            </div>
            <div className="flex gap-3">
              <button onClick={onClose} className="flex-1 px-4 py-2 text-sm font-medium text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors">
                Cancel
              </button>
              <button
                onClick={startSetup}
                disabled={starting}
                className="flex-1 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-60 rounded-lg transition-colors flex items-center justify-center gap-2"
              >
                {starting ? <Loader2 size={14} className="animate-spin" /> : <ExternalLink size={14} />}
                {starting ? 'Opening…' : 'Open Setup'}
              </button>
            </div>
          </>
        )}

        {step === 'waiting' && (
          <>
            <div className="flex flex-col items-center py-4 gap-3">
              <Loader2 size={32} className="animate-spin text-blue-600" />
              <p className="text-sm font-medium text-slate-700">Waiting for QuickBooks…</p>
              <p className="text-xs text-slate-400 text-center leading-relaxed">{statusMsg}</p>
            </div>
            <p className="text-xs text-slate-400 text-center mb-4">
              Complete the steps in the browser window, then authorize in QuickBooks when prompted.
            </p>
            {authorizationUrl && (
              <a href={authorizationUrl} target="_blank" rel="noopener noreferrer"
                className="w-full px-4 py-2 text-sm font-medium text-blue-600 border border-blue-200 rounded-lg hover:bg-blue-50 transition-colors flex items-center justify-center gap-1.5">
                <ExternalLink size={13} /> Open QuickBooks authorization
              </a>
            )}
          </>
        )}

        {step === 'done' && (
          <div className="flex flex-col items-center py-4 gap-3 text-center">
            <div className="w-12 h-12 rounded-full bg-emerald-100 flex items-center justify-center">
              <Check size={24} className="text-emerald-600" />
            </div>
            <p className="text-sm font-bold text-slate-800">QuickBooks authorization is active</p>
            <p className="text-xs text-slate-500">The provider reports an active connection. First successful data sync and freshness still require verification.</p>
            <button onClick={onClose} className="mt-2 px-6 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors">
              Done
            </button>
          </div>
        )}

        {step === 'error' && (
          <div className="flex flex-col items-center py-4 gap-3 text-center">
            <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center">
              <AlertTriangle size={24} className="text-red-500" />
            </div>
            <p className="text-sm font-bold text-slate-800">Setup failed</p>
            <p className="text-xs text-slate-400">{errorMsg}</p>
            <div className="flex gap-3 mt-2 w-full">
              <button onClick={onClose} className="flex-1 px-4 py-2 text-sm font-medium text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors">Cancel</button>
              <button onClick={() => { setStep('start'); setErrorMsg(''); }} className="flex-1 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors">Try Again</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Integration row ──────────────────────────────────────────────────────────

interface IntegrationRowProps {
  label: string;
  description: string;
  connected: boolean;
  loading: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
  connectLabel?: string;
  canManage: boolean;
}

function IntegrationRow({
  label, description, connected, loading, onConnect, onDisconnect, connectLabel = 'Connect', canManage,
}: IntegrationRowProps) {
  return (
    <div className="flex flex-wrap items-center gap-4 py-4 border-b border-slate-100 last:border-0">
      <div className="flex-1">
        <div className="text-sm font-medium text-slate-800">{label}</div>
        <div className="text-xs text-slate-400 mt-0.5">{description}</div>
      </div>

      <div className={clsx(
        'flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-lg flex-shrink-0',
        connected ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500',
      )}>
        {loading
          ? <Loader2 size={11} className="animate-spin" />
          : connected ? <Check size={11} /> : <AlertTriangle size={11} />}
        {loading ? 'Checking…' : connected ? 'Connected' : 'Not connected'}
      </div>

      {loading ? null : !canManage ? (
        <span className="text-xs text-slate-500">Owner permission required</span>
      ) : connected ? (
        <button
          onClick={onDisconnect}
          className="text-xs text-red-500 hover:text-red-600 font-medium border border-red-100 rounded-lg px-2.5 py-1.5 bg-red-50 hover:bg-red-100 transition-colors flex-shrink-0"
        >
          Disconnect
        </button>
      ) : (
        <button
          onClick={onConnect}
          className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700 font-medium border border-blue-200 rounded-lg px-2.5 py-1.5 bg-blue-50 hover:bg-blue-100 transition-colors flex-shrink-0"
        >
          {connectLabel} <ExternalLink size={11} />
        </button>
      )}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function Settings() {
  const { role } = useAuth();
  const sections = role === 'owner' ? SECTIONS : SECTIONS.filter((section) => section.id !== 'billing');
  const [active, setActive] = useState('integrations');

  // Integration state
  const [integrations, setIntegrations] = useState<IntegrationsResponse | null>(null);
  const [integLoading, setIntegLoading]   = useState(true);
  const [integError, setIntegError] = useState(false);
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});
  const [showQBDModal, setShowQBDModal] = useState(false);

  // ── Load integration statuses on mount ────────────────────────────────────
  const loadIntegrations = useCallback(async () => {
    setIntegLoading(true);
    try {
      const data = await api.settings.integrations();
      setIntegrations(data);
      setIntegError(false);
    } catch {
      setIntegrations(null);
      setIntegError(true);
    } finally {
      setIntegLoading(false);
    }
  }, []);

  useEffect(() => { loadIntegrations(); }, [loadIntegrations]);

  // ── Handle OAuth return params (?qbo=connected, ?gmail=connected, etc.) ──
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const qbo   = params.get('qbo');
    const gmail = params.get('gmail');
    if (qbo === 'connected') {
      toast('Returned from QuickBooks authorization. Verify the status below.');
      loadIntegrations();
    } else if (qbo === 'error') {
      toast.error('QuickBooks authorization was not completed.');
    }

    if (gmail === 'connected') {
      toast('Returned from Google authorization. Verify the status below.');
      loadIntegrations();
    } else if (gmail === 'error') {
      toast.error('Google authorization was not completed.');
    }

    // Clean up URL params without triggering a reload
    if (qbo || gmail) {
      const clean = new URL(window.location.href);
      clean.searchParams.delete('qbo');
      clean.searchParams.delete('gmail');
      clean.searchParams.delete('reason');
      window.history.replaceState({}, '', clean.toString());
    }
  }, [loadIntegrations]);

  // ── Helpers ───────────────────────────────────────────────────────────────
  const setLoading = (key: string, val: boolean) =>
    setActionLoading((prev) => ({ ...prev, [key]: val }));

  const connectQBO = async () => {
    setLoading('qbo', true);
    try {
      const { url } = await api.settings.qboConnectUrl();
      if (!isTrustedConsentUrl(url, 'appcenter.intuit.com')) throw new Error('Untrusted consent URL');
      window.location.href = url;
    } catch {
      toast.error('Could not start QuickBooks connection');
      setLoading('qbo', false);
    }
  };

  const disconnectQBO = async () => {
    setLoading('qbo', true);
    try {
      await api.settings.qboDisconnect();
      toast.success('QuickBooks Online disconnected');
      await loadIntegrations();
    } catch {
      toast.error('Failed to disconnect QuickBooks');
    } finally {
      setLoading('qbo', false);
    }
  };

  const onQBDConnected = async () => {
    toast('QuickBooks Desktop authorization active. First sync and data freshness still need verification.');
    await loadIntegrations();
  };

  const disconnectQBD = async () => {
    setLoading('qbd', true);
    try {
      await api.settings.disconnectQBD();
      toast.success('QuickBooks Desktop disconnected');
      await loadIntegrations();
    } catch {
      toast.error('Failed to disconnect QuickBooks Desktop');
    } finally {
      setLoading('qbd', false);
    }
  };

  const connectGmail = async () => {
    setLoading('gmail', true);
    try {
      const { url } = await api.settings.gmailConnectUrl();
      if (!isTrustedConsentUrl(url, 'accounts.google.com')) throw new Error('Untrusted consent URL');
      window.location.href = url;
    } catch {
      toast.error('Could not start Google connection');
      setLoading('gmail', false);
    }
  };

  const disconnectGmail = async () => {
    setLoading('gmail', true);
    try {
      await api.settings.gmailDisconnect();
      toast.success('Gmail & Google Sheets disconnected');
      await loadIntegrations();
    } catch {
      toast.error('Failed to disconnect Google');
    } finally {
      setLoading('gmail', false);
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────────
  const activeSection = sections.find((s) => s.id === active) ?? sections[0];
  const ActiveIcon = activeSection.icon;

  return (
    <div className="flex h-full flex-col md:flex-row bg-slate-50">
      {/* Sidebar */}
      <div className="md:w-52 flex-shrink-0 bg-white border-r border-slate-100 pt-3 md:pt-6">
        <div className="px-4 mb-4">
          <div className="flex items-center gap-2 text-slate-800">
            <SettingsIcon size={16} />
            <span className="text-sm font-bold">Settings</span>
          </div>
        </div>
        <nav className="px-2 flex gap-1 overflow-x-auto md:block md:space-y-0.5" aria-label="Settings sections">
          {sections.map((s) => {
            const Icon = s.icon;
            return (
              <button
                key={s.id}
                onClick={() => setActive(s.id)}
                className={clsx(
                  'shrink-0 md:w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors text-left',
                  active === s.id
                    ? 'bg-slate-800 text-white'
                    : 'text-slate-500 hover:bg-slate-50 hover:text-slate-800',
                )}
              >
                <Icon size={14} />
                {s.label}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 overflow-auto p-4 md:p-8">
        <div className="max-w-2xl">
          <div className="flex items-center gap-2.5 mb-6">
            <ActiveIcon size={18} className="text-slate-600" />
            <h1 className="text-lg font-bold text-slate-900">{activeSection.label}</h1>
          </div>

          <div className="bg-white rounded-xl border border-slate-100 shadow-sm px-5">
            {active === 'integrations' && (
              <>
                {integError ? (
                  <p role="alert" className="my-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
                    Integration status unavailable. Connection states are unknown; retry before taking any action.
                  </p>
                ) : (
                <>
                {/* QB Online */}
                <IntegrationRow
                  label="QuickBooks Online"
                  description="Cloud-based accounting sync — invoices, payments, and customers via OAuth"
                  connected={integrations?.quickbooks_online.connected ?? false}
                  loading={integLoading || !!actionLoading['qbo']}
                  onConnect={connectQBO}
                  onDisconnect={disconnectQBO}
                  connectLabel="Connect with Intuit"
                  canManage={role === 'owner'}
                />

                {/* QB Desktop */}
                <IntegrationRow
                  label="QuickBooks Desktop"
                  description="On-premise QuickBooks — guided setup connects in minutes, no technical knowledge needed"
                  connected={integrations?.quickbooks_desktop.connected ?? false}
                  loading={integLoading || !!actionLoading['qbd']}
                  onConnect={() => setShowQBDModal(true)}
                  onDisconnect={disconnectQBD}
                  connectLabel="Set up"
                  canManage={role === 'owner'}
                />

                {/* Gmail */}
                <IntegrationRow
                  label="Gmail"
                  description="Monitor and respond to customer emails via your Google account"
                  connected={integrations?.gmail.connected ?? false}
                  loading={integLoading || !!actionLoading['gmail']}
                  onConnect={connectGmail}
                  onDisconnect={disconnectGmail}
                  connectLabel="Connect with Google"
                  canManage={role === 'owner'}
                />

                {/* Google Sheets — shares Gmail token */}
                <IntegrationRow
                  label="Google Sheets"
                  description="Export reports and sync data — uses the same Google account as Gmail"
                  connected={integrations?.google_sheets.connected ?? false}
                  loading={integLoading || !!actionLoading['gmail']}
                  onConnect={connectGmail}
                  onDisconnect={disconnectGmail}
                  connectLabel="Connect with Google"
                  canManage={role === 'owner'}
                />
                </>
                )}

                <p className="text-xs text-slate-400 py-3">
                  AI model, database, and email delivery credentials are managed by SecretaryAI — you never need to enter them.
                </p>
                <p className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded p-3 mb-3">
                  Notification preferences, inventory thresholds and desktop-agent settings are not available yet.
                  Their previous on-screen switches did not persist any configuration, so they are withheld pending a verified settings API.
                </p>
              </>
            )}

          </div>

          {active === 'security' && (
            <TwoFASection />
          )}
          {active === 'billing' && role === 'owner' && (
            <div className="rounded-xl border border-slate-200 bg-white px-5">
              <BillingStatus />
            </div>
          )}
        </div>
      </div>

      {/* QB Desktop setup modal */}
      {showQBDModal && (
        <QBDSetupModal
          onClose={() => setShowQBDModal(false)}
          onConnected={onQBDConnected}
        />
      )}
    </div>
  );
}
