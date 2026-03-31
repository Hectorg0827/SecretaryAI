import React, { useEffect, useState, useCallback } from 'react';
import {
  Wifi, WifiOff, AlertTriangle, RefreshCw, Copy, Check, ChevronDown, ChevronRight,
  Terminal, Clock, Zap, Server, Activity,
} from 'lucide-react';
import { api, ConnectorRegistration, ConnectorSyncLog } from '../lib/api';
import toast from 'react-hot-toast';

// ── Types ─────────────────────────────────────────────────────────────────────

interface ConnectorDetail {
  connector: ConnectorRegistration;
  sync_logs: ConnectorSyncLog[];
}

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: ConnectorRegistration['status'] }) {
  const styles: Record<string, string> = {
    connected:    'bg-emerald-100 text-emerald-700 border-emerald-200',
    stale:        'bg-amber-100  text-amber-700  border-amber-200',
    error:        'bg-red-100    text-red-700    border-red-200',
    disconnected: 'bg-slate-100  text-slate-600  border-slate-200',
  };
  const icons: Record<string, React.ReactNode> = {
    connected:    <Wifi size={11} />,
    stale:        <Clock size={11} />,
    error:        <AlertTriangle size={11} />,
    disconnected: <WifiOff size={11} />,
  };
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${styles[status] ?? styles.disconnected}`}>
      {icons[status] ?? icons.disconnected}
      {status}
    </span>
  );
}

// ── Relative time ─────────────────────────────────────────────────────────────

function relativeTime(iso: string | null): string {
  if (!iso) return 'never';
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60)  return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// ── Connector row ─────────────────────────────────────────────────────────────

function ConnectorRow({ reg }: { reg: ConnectorRegistration }) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<ConnectorDetail | null>(null);
  const [loading, setLoading] = useState(false);

  const toggle = async () => {
    if (!expanded && !detail) {
      setLoading(true);
      try {
        const d = await api.connectors.detail(reg.id);
        setDetail(d);
      } catch {
        toast.error('Failed to load connector details');
      } finally {
        setLoading(false);
      }
    }
    setExpanded((e) => !e);
  };

  return (
    <div className="border border-slate-200 rounded-xl overflow-hidden">
      {/* Header row */}
      <button
        onClick={toggle}
        className="w-full flex items-center gap-3 px-5 py-4 bg-white hover:bg-slate-50 text-left transition-colors"
      >
        <Server size={16} className="text-slate-400 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-slate-800 text-sm">{reg.connector_type}</span>
            <span className="text-slate-400 text-xs font-mono">{reg.connector_id}</span>
          </div>
          <div className="flex items-center gap-3 mt-0.5">
            <StatusBadge status={reg.status} />
            <span className="text-xs text-slate-400">
              Last heartbeat: {relativeTime(reg.last_heartbeat)}
            </span>
            <span className="text-xs text-slate-400">v{reg.version}</span>
          </div>
        </div>
        {loading ? (
          <RefreshCw size={14} className="text-slate-400 animate-spin" />
        ) : expanded ? (
          <ChevronDown size={14} className="text-slate-400" />
        ) : (
          <ChevronRight size={14} className="text-slate-400" />
        )}
      </button>

      {/* Expanded detail */}
      {expanded && detail && (
        <div className="border-t border-slate-100 bg-slate-50 px-5 py-4 space-y-4">
          {/* Capabilities */}
          <div>
            <div className="text-xs font-medium text-slate-500 mb-2">Capabilities</div>
            <div className="flex flex-wrap gap-1.5">
              {(reg.capabilities ?? []).map((cap) => (
                <span key={cap} className="px-2 py-0.5 bg-blue-50 text-blue-700 text-xs rounded-md border border-blue-100 font-mono">
                  {cap}
                </span>
              ))}
            </div>
          </div>

          {/* Recent sync logs */}
          <div>
            <div className="text-xs font-medium text-slate-500 mb-2">Recent Syncs</div>
            {detail.sync_logs.length === 0 ? (
              <p className="text-xs text-slate-400 italic">No sync logs yet.</p>
            ) : (
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                {detail.sync_logs.map((log) => (
                  <div key={log.id} className="flex items-center gap-3 text-xs bg-white rounded-lg px-3 py-2 border border-slate-100">
                    <Activity size={11} className={log.status === 'ok' ? 'text-emerald-500' : 'text-red-400'} />
                    <span className="font-medium text-slate-700 w-28 truncate">{log.entity_type}</span>
                    <span className="text-slate-400">{log.rows_fetched ?? 0} fetched</span>
                    <span className="text-slate-400">{log.rows_upserted ?? 0} upserted</span>
                    <span className="ml-auto text-slate-400">{relativeTime(log.started_at)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Install guide ─────────────────────────────────────────────────────────────

function InstallGuide({ secret }: { secret: string }) {
  const [copied, setCopied] = useState(false);

  const copy = (text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const envBlock = `SECRETARY_CLOUD_URL=https://app.secretaryai.com
SECRETARY_INSTALL_SECRET=${secret}
SECRETARY_COMPANY_ID=<your-company-id>
CONDUCTOR_API_KEY=<your-conductor-key>
CONDUCTOR_END_USER_ID=<your-end-user-id>`;

  return (
    <div className="bg-slate-900 rounded-xl p-5 space-y-4">
      <div className="flex items-center gap-2 text-slate-300 text-sm font-medium">
        <Terminal size={14} />
        Installation Steps
      </div>

      <div className="space-y-3 text-sm">
        {[
          { step: '1', label: 'Download the connector', cmd: 'pip install secretaryai-connector' },
          { step: '2', label: 'Create a .env file in the connector directory', cmd: envBlock },
          { step: '3', label: 'Run the connector', cmd: 'python -m connector.main' },
        ].map(({ step, label, cmd }) => (
          <div key={step} className="space-y-1.5">
            <div className="text-slate-400 text-xs">
              <span className="text-blue-400 font-medium">Step {step}:</span> {label}
            </div>
            <div className="relative group">
              <pre className="bg-black/40 rounded-lg px-4 py-2.5 text-slate-300 font-mono text-xs overflow-x-auto whitespace-pre-wrap break-all">
                {cmd}
              </pre>
              <button
                onClick={() => copy(cmd)}
                className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 p-1 rounded bg-slate-700 hover:bg-slate-600 transition-all"
              >
                {copied ? <Check size={11} className="text-emerald-400" /> : <Copy size={11} className="text-slate-300" />}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function Connectors() {
  const [connectors, setConnectors] = useState<ConnectorRegistration[]>([]);
  const [loading, setLoading] = useState(true);
  const [secret, setSecret] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [showGuide, setShowGuide] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api.connectors.status();
      setConnectors(data.connectors ?? []);
    } catch {
      toast.error('Failed to load connector status');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  const generateSecret = async () => {
    if (!confirm('This will rotate the install secret. Any un-installed connectors using the old secret will fail. Continue?')) return;
    setGenerating(true);
    try {
      const res = await api.connectors.generateSecret();
      setSecret(res.install_secret);
      setShowGuide(true);
      toast.success('New install secret generated');
    } catch {
      toast.error('Failed to generate install secret');
    } finally {
      setGenerating(false);
    }
  };

  const connected = connectors.filter((c) => c.status === 'connected').length;
  const stale = connectors.filter((c) => c.status === 'stale').length;
  const errored = connectors.filter((c) => c.status === 'error').length;

  return (
    <div className="h-full overflow-y-auto bg-slate-50">
      <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">

        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-slate-900">Connectors</h1>
            <p className="text-sm text-slate-500 mt-0.5">
              Manage local QB Desktop connectors that sync data to the cloud
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={load}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm text-slate-600 border border-slate-200 hover:bg-white transition-colors"
            >
              <RefreshCw size={13} />
              Refresh
            </button>
            <button
              onClick={generateSecret}
              disabled={generating}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              <Zap size={13} />
              {generating ? 'Generating…' : 'New Install Secret'}
            </button>
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-4">
          {[
            { label: 'Connected',     value: connected, color: 'text-emerald-600', bg: 'bg-emerald-50 border-emerald-100' },
            { label: 'Stale',         value: stale,     color: 'text-amber-600',   bg: 'bg-amber-50 border-amber-100'   },
            { label: 'Error',         value: errored,   color: 'text-red-600',     bg: 'bg-red-50 border-red-100'       },
          ].map(({ label, value, color, bg }) => (
            <div key={label} className={`rounded-xl border p-4 ${bg}`}>
              <div className={`text-2xl font-bold ${color}`}>{value}</div>
              <div className="text-xs text-slate-500 mt-0.5">{label}</div>
            </div>
          ))}
        </div>

        {/* Install guide (shown after secret generation) */}
        {showGuide && secret && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-700">Installation Guide</h2>
              <button
                onClick={() => setShowGuide(false)}
                className="text-xs text-slate-400 hover:text-slate-600"
              >
                Dismiss
              </button>
            </div>
            <InstallGuide secret={secret} />
          </div>
        )}

        {/* Connector list */}
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-700">
            {connectors.length === 0 ? 'No connectors registered' : `${connectors.length} connector${connectors.length !== 1 ? 's' : ''}`}
          </h2>

          {loading ? (
            <div className="flex items-center justify-center py-12 text-slate-400 text-sm gap-2">
              <RefreshCw size={14} className="animate-spin" />
              Loading…
            </div>
          ) : connectors.length === 0 ? (
            <div className="rounded-xl border-2 border-dashed border-slate-200 bg-white px-8 py-12 text-center space-y-3">
              <WifiOff size={32} className="mx-auto text-slate-300" />
              <div className="text-slate-500 text-sm">No connectors registered yet.</div>
              <div className="text-slate-400 text-xs max-w-xs mx-auto">
                Generate an install secret above, then follow the instructions to install the
                QB Desktop connector on your Windows machine.
              </div>
            </div>
          ) : (
            connectors.map((c) => <ConnectorRow key={c.id} reg={c} />)
          )}
        </div>

      </div>
    </div>
  );
}
