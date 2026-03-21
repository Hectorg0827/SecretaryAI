import React, { useState, useEffect, useCallback } from 'react';
import clsx from 'clsx';
import {
  Settings as SettingsIcon, Database, Mail, Package, Zap,
  Check, AlertTriangle, Loader2, X, ExternalLink,
} from 'lucide-react';
import { api, IntegrationsResponse } from '../lib/api';
import toast from 'react-hot-toast';

interface Section { id: string; label: string; icon: React.ElementType; }

const SECTIONS: Section[] = [
  { id: 'integrations', label: 'Integrations',    icon: Database },
  { id: 'email',        label: 'Email Settings',   icon: Mail     },
  { id: 'inventory',    label: 'Inventory Alerts', icon: Package  },
  { id: 'agent',        label: 'Desktop Agent',    icon: Zap      },
];

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

// ─── QB Desktop modal ─────────────────────────────────────────────────────────

function QBDModal({ onClose, onSave }: { onClose: () => void; onSave: (id: string) => Promise<void> }) {
  const [endUserId, setEndUserId] = useState('');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    const id = endUserId.trim();
    if (!id) { toast.error('End User ID is required'); return; }
    setSaving(true);
    try {
      await onSave(id);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="bg-white rounded-xl shadow-xl border border-slate-100 w-full max-w-md p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-bold text-slate-900">Connect QuickBooks Desktop</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={16} />
          </button>
        </div>

        <p className="text-sm text-slate-600 mb-1">
          Enter the <strong>Conductor End User ID</strong> for your QuickBooks Desktop company.
          You can find this in your Conductor dashboard under <em>End Users</em>.
        </p>
        <p className="text-xs text-slate-400 mb-4">
          Your QuickBooks data and API keys are managed securely by SecretaryAI — you only need this ID.
        </p>

        <label className="block text-xs font-medium text-slate-700 mb-1">End User ID</label>
        <input
          type="text"
          value={endUserId}
          onChange={(e) => setEndUserId(e.target.value)}
          placeholder="eu_xxxxxxxxxxxxxxxxxxxx"
          className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400 font-mono"
          autoFocus
          onKeyDown={(e) => e.key === 'Enter' && handleSave()}
        />

        <div className="flex gap-3 mt-5">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2 text-sm font-medium text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex-1 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-60 rounded-lg transition-colors flex items-center justify-center gap-2"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : null}
            {saving ? 'Connecting…' : 'Connect'}
          </button>
        </div>
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
  const [showQBDModal, setShowQBDModal]   = useState(false);

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

  const saveQBD = async (endUserId: string) => {
    setLoading('qbd', true);
    try {
      await api.settings.saveQBD(endUserId);
      toast.success('QuickBooks Desktop connected');
      await loadIntegrations();
    } catch {
      toast.error('Failed to connect QuickBooks Desktop');
      throw new Error('save failed'); // keep modal open
    } finally {
      setLoading('qbd', false);
    }
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
                  description="On-premise QuickBooks via Conductor — enter your End User ID to connect"
                  connected={integrations?.quickbooks_desktop.connected ?? false}
                  loading={integLoading || !!actionLoading['qbd']}
                  onConnect={() => setShowQBDModal(true)}
                  onDisconnect={disconnectQBD}
                  connectLabel="Enter ID"
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

          {active !== 'integrations' && (
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

      {/* QB Desktop modal */}
      {showQBDModal && (
        <QBDModal
          onClose={() => setShowQBDModal(false)}
          onSave={saveQBD}
        />
      )}
    </div>
  );
}
