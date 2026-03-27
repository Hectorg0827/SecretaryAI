import React, { useState, useEffect, useCallback, useRef } from 'react';
import clsx from 'clsx';
import {
  Settings as SettingsIcon, Database, Mail, Package, Zap, ShieldCheck,
  Check, AlertTriangle, Loader2, X, ExternalLink, Copy,
} from 'lucide-react';
import { api, IntegrationsResponse } from '../lib/api';
import toast from 'react-hot-toast';

interface Section { id: string; label: string; icon: React.ElementType; }

const SECTIONS: Section[] = [
  { id: 'integrations', label: 'Integrations',    icon: Database     },
  { id: 'email',        label: 'Email Settings',   icon: Mail         },
  { id: 'inventory',    label: 'Inventory Alerts', icon: Package      },
  { id: 'agent',        label: 'Desktop Agent',    icon: Zap          },
  { id: 'security',     label: 'Security',         icon: ShieldCheck  },
];

// ─── Two-Factor Authentication section ────────────────────────────────────────

function TwoFASection() {
  type Step = 'idle' | 'setup' | 'verify' | 'enabled';

  const [step,     setStep]     = useState<Step>('idle');
  const [secret,   setSecret]   = useState('');
  const [qrUrl,    setQrUrl]    = useState('');
  const [code,     setCode]     = useState('');
  const [loading,  setLoading]  = useState(false);
  const [enabled,  setEnabled]  = useState(false);
  const [disCode,  setDisCode]  = useState('');
  const [disOpen,  setDisOpen]  = useState(false);

  // Start 2FA setup — request a new TOTP secret from the backend
  const startSetup = async () => {
    setLoading(true);
    try {
      const res = await api.post<{ secret: string; otpauth_url: string }>('/auth/2fa/setup', {});
      setSecret(res.secret);
      setQrUrl(res.otpauth_url);
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
      setStep('enabled');
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
        {step === 'idle' && !enabled && (
          <button
            onClick={startSetup}
            disabled={loading}
            className="px-3 py-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors disabled:opacity-50 flex items-center gap-1.5"
          >
            {loading && <Loader2 size={11} className="animate-spin" />}
            Enable
          </button>
        )}
        {enabled && (
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
            Scan this QR code with your authenticator app (e.g. Google Authenticator or Authy), then enter the 6-digit code below to confirm.
          </p>

          {/* QR code rendered as a link to an online renderer to keep bundle size down */}
          <div className="flex items-center gap-4">
            <div className="w-32 h-32 border border-slate-200 rounded-lg flex items-center justify-center bg-slate-50 flex-shrink-0">
              <img
                src={`https://api.qrserver.com/v1/create-qr-code/?size=120x120&data=${encodeURIComponent(qrUrl)}`}
                alt="2FA QR code"
                className="w-28 h-28 rounded"
              />
            </div>
            <div className="min-w-0">
              <p className="text-xs text-slate-500 mb-1">Or enter this secret manually:</p>
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
            <button onClick={() => setStep('idle')} className="text-xs text-slate-400 hover:text-slate-600 px-2">
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

function Toggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!value)}
      className={clsx(
        'relative w-10 h-5 rounded-full transition-colors flex-shrink-0',
        value ? 'bg-blue-600' : 'bg-slate-200',
      )}
    >
      <span className={clsx(
        'absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform',
        value && 'translate-x-5',
      )} />
    </button>
  );
}

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
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Clean up polling when modal unmounts
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const startSetup = async () => {
    setStarting(true);
    try {
      const data = await api.setup.qbdStart();
      // Open Conductor's hosted auth flow in a new tab
      window.open(data.auth_flow_url, '_blank', 'noopener,noreferrer');
      setStep('waiting');
      setStatusMsg('Waiting for QuickBooks authorization…');
      beginPolling();
    } catch (e: any) {
      setErrorMsg(e.message ?? 'Could not start setup. Check that Conductor is configured.');
      setStep('error');
    } finally {
      setStarting(false);
    }
  };

  const beginPolling = () => {
    pollRef.current = setInterval(async () => {
      try {
        const status = await api.setup.qbdStatus();
        setStatusMsg(status.message);
        if (status.connected) {
          clearInterval(pollRef.current!);
          setStep('done');
          onConnected();
        }
      } catch {
        // Ignore transient errors — keep polling
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
            <button onClick={startSetup} className="w-full px-4 py-2 text-sm font-medium text-blue-600 border border-blue-200 rounded-lg hover:bg-blue-50 transition-colors flex items-center justify-center gap-1.5">
              <ExternalLink size={13} /> Re-open setup page
            </button>
          </>
        )}

        {step === 'done' && (
          <div className="flex flex-col items-center py-4 gap-3 text-center">
            <div className="w-12 h-12 rounded-full bg-emerald-100 flex items-center justify-center">
              <Check size={24} className="text-emerald-600" />
            </div>
            <p className="text-sm font-bold text-slate-800">QuickBooks is connected!</p>
            <p className="text-xs text-slate-400">SecretaryAI will now sync your data automatically.</p>
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
}

function IntegrationRow({
  label, description, connected, loading, onConnect, onDisconnect, connectLabel = 'Connect',
}: IntegrationRowProps) {
  return (
    <div className="flex items-center gap-4 py-4 border-b border-slate-100 last:border-0">
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
        {connected ? 'Connected' : 'Not connected'}
      </div>

      {loading ? null : connected ? (
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
  const [active, setActive] = useState('integrations');

  // Integration state
  const [integrations, setIntegrations] = useState<IntegrationsResponse | null>(null);
  const [integLoading, setIntegLoading]   = useState(true);
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});
  const [showQBDModal, setShowQBDModal] = useState(false);

  // Email / inventory / agent toggles
  const [emailSummarize, setEmailSummarize]       = useState(true);
  const [emailNotify,    setEmailNotify]           = useState(true);
  const [invLow,         setInvLow]               = useState(true);
  const [invCritical,    setInvCritical]           = useState(true);
  const [invOutOfStock,  setInvOutOfStock]         = useState(true);
  const [lowThreshold,   setLowThreshold]          = useState('8');
  const [criticalThreshold, setCriticalThreshold]  = useState('4');

  // ── Load integration statuses on mount ────────────────────────────────────
  const loadIntegrations = useCallback(async () => {
    setIntegLoading(true);
    try {
      const data = await api.settings.integrations();
      setIntegrations(data);
    } catch {
      toast.error('Could not load integration status');
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
    const reason = params.get('reason');

    if (qbo === 'connected') {
      toast.success('QuickBooks Online connected!');
      loadIntegrations();
    } else if (qbo === 'error') {
      toast.error(`QuickBooks connection failed${reason ? `: ${reason}` : ''}`);
    }

    if (gmail === 'connected') {
      toast.success('Gmail & Google Sheets connected!');
      loadIntegrations();
    } else if (gmail === 'error') {
      toast.error(`Google connection failed${reason ? `: ${reason}` : ''}`);
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
    toast.success('QuickBooks Desktop connected!');
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
  const activeSection = SECTIONS.find((s) => s.id === active)!;
  const ActiveIcon = activeSection.icon;

  return (
    <div className="flex h-screen bg-slate-50">
      {/* Sidebar */}
      <div className="w-52 flex-shrink-0 bg-white border-r border-slate-100 pt-6">
        <div className="px-4 mb-4">
          <div className="flex items-center gap-2 text-slate-800">
            <SettingsIcon size={16} />
            <span className="text-sm font-bold">Settings</span>
          </div>
        </div>
        <nav className="px-2 space-y-0.5">
          {SECTIONS.map((s) => {
            const Icon = s.icon;
            return (
              <button
                key={s.id}
                onClick={() => setActive(s.id)}
                className={clsx(
                  'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors text-left',
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
      <div className="flex-1 overflow-auto p-8">
        <div className="max-w-2xl">
          <div className="flex items-center gap-2.5 mb-6">
            <ActiveIcon size={18} className="text-slate-600" />
            <h1 className="text-lg font-bold text-slate-900">{activeSection.label}</h1>
          </div>

          <div className="bg-white rounded-xl border border-slate-100 shadow-sm px-5">
            {active === 'integrations' && (
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
                />

                <p className="text-xs text-slate-400 py-3">
                  AI model, database, and email delivery credentials are managed by SecretaryAI — you never need to enter them.
                </p>
              </>
            )}

            {active === 'email' && (
              <>
                <SettingRow label="AI Email Summarization" description="Automatically summarize incoming emails with priority and action needed">
                  <Toggle value={emailSummarize} onChange={setEmailSummarize} />
                </SettingRow>
                <SettingRow label="Email Notifications" description="Send desktop notifications for high-priority emails">
                  <Toggle value={emailNotify} onChange={setEmailNotify} />
                </SettingRow>
                <SettingRow label="Smart Reply Model" description="AI model used for drafting replies">
                  <select className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white">
                    <option>claude-sonnet-4-6</option>
                    <option>claude-haiku-4-5</option>
                  </select>
                </SettingRow>
              </>
            )}

            {active === 'inventory' && (
              <>
                <SettingRow label="Low Stock Alert" description="Alert when stock drops below threshold weeks of supply">
                  <div className="flex items-center gap-2">
                    <Toggle value={invLow} onChange={setInvLow} />
                    <input
                      type="number"
                      value={lowThreshold}
                      onChange={(e) => setLowThreshold(e.target.value)}
                      className="w-16 text-sm border border-slate-200 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-blue-400 text-center"
                    />
                    <span className="text-xs text-slate-400">wks</span>
                  </div>
                </SettingRow>
                <SettingRow label="Critical Stock Alert" description="Alert when stock drops to critical level">
                  <div className="flex items-center gap-2">
                    <Toggle value={invCritical} onChange={setInvCritical} />
                    <input
                      type="number"
                      value={criticalThreshold}
                      onChange={(e) => setCriticalThreshold(e.target.value)}
                      className="w-16 text-sm border border-slate-200 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-blue-400 text-center"
                    />
                    <span className="text-xs text-slate-400">wks</span>
                  </div>
                </SettingRow>
                <SettingRow label="Out-of-Stock Alert" description="Alert immediately when any item reaches zero">
                  <Toggle value={invOutOfStock} onChange={setInvOutOfStock} />
                </SettingRow>
              </>
            )}

            {active === 'agent' && (
              <>
                <SettingRow label="Auto-start on Login" description="Launch desktop agent when you log into your computer">
                  <Toggle value={true} onChange={() => {}} />
                </SettingRow>
                <SettingRow label="Watched Folders" description="Folders scanned every 30 minutes for CSV/Excel reports">
                  <button className="text-xs text-blue-600 hover:text-blue-700 font-medium border border-blue-200 rounded-lg px-2.5 py-1.5 bg-blue-50 hover:bg-blue-100 transition-colors">
                    Manage folders
                  </button>
                </SettingRow>
                <SettingRow label="Heartbeat Interval" description="How often the agent checks in with the server">
                  <select className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white">
                    <option>5 minutes</option>
                    <option>10 minutes</option>
                    <option>15 minutes</option>
                  </select>
                </SettingRow>
              </>
            )}
          </div>

          {active === 'security' && (
            <TwoFASection />
          )}

          {active !== 'integrations' && active !== 'security' && (
            <div className="mt-5 flex justify-end">
              <button
                onClick={() => toast.success('Settings saved')}
                className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors"
              >
                Save Changes
              </button>
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
