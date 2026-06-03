import React, { useEffect, useState, useCallback } from 'react';
import {
  Activity, RefreshCw, Server, GitBranch, Cpu, HardDrive,
  CheckCircle2, XCircle, Clock, AlertTriangle, ChevronLeft, ChevronRight,
  type LucideIcon,
} from 'lucide-react';
import { api } from '../lib/api';

// ── Types ─────────────────────────────────────────────────────────────────────

interface ConnectorHealth {
  connected: number; stale: number; error: number; total: number;
}
interface WorkflowHealth {
  running: number; awaiting_approval: number; completed: number; failed: number; total_24h: number;
}
interface CuHealth {
  pending: number; running: number; completed: number; failed: number; total_24h: number;
}
interface IngestionHealth {
  pending: number; processing: number; done: number; error: number; total_24h: number;
}
interface SystemHealth {
  company_id: string;
  generated_at: string;
  connectors: ConnectorHealth;
  workflow_runs: WorkflowHealth;
  computer_use: CuHealth;
  ingestion: IngestionHealth;
  audit_events_24h: number;
}

interface WorkflowRun {
  id: string;
  workflow_name: string;
  status: string;
  created_at: string;
  updated_at: string;
  step_results?: unknown[];
}

interface WorkflowRunsResponse {
  runs: WorkflowRun[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function relativeTime(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60)   return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ── Stat card ─────────────────────────────────────────────────────────────────

function StatCard({
  label, value, sub, color = 'blue',
}: {
  label: string; value: number | string; sub?: string; color?: 'blue' | 'green' | 'red' | 'amber' | 'slate';
}) {
  const colors = {
    blue:  'bg-blue-50  text-blue-700  border-blue-100',
    green: 'bg-emerald-50 text-emerald-700 border-emerald-100',
    red:   'bg-red-50   text-red-700   border-red-100',
    amber: 'bg-amber-50 text-amber-700 border-amber-100',
    slate: 'bg-slate-50 text-slate-600 border-slate-200',
  };
  return (
    <div className={`rounded-lg border p-3 ${colors[color]}`}>
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-xs font-medium mt-0.5">{label}</div>
      {sub && <div className="text-[10px] opacity-70 mt-0.5">{sub}</div>}
    </div>
  );
}

// ── Section header ────────────────────────────────────────────────────────────

function SectionHeader({ icon: Icon, title, sub }: { icon: LucideIcon; title: string; sub?: string }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <Icon size={16} className="text-slate-500" />
      <div>
        <div className="text-sm font-semibold text-slate-700">{title}</div>
        {sub && <div className="text-xs text-slate-400">{sub}</div>}
      </div>
    </div>
  );
}

// ── Connector section ─────────────────────────────────────────────────────────

function ConnectorSection({ h }: { h: ConnectorHealth }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4">
      <SectionHeader icon={Server} title="Connectors" />
      <div className="grid grid-cols-3 gap-2">
        <StatCard label="Connected" value={h.connected} color="green" />
        <StatCard label="Stale"     value={h.stale}     color="amber" />
        <StatCard label="Error"     value={h.error}     color="red"   />
      </div>
    </div>
  );
}

// ── Workflow section ──────────────────────────────────────────────────────────

function WorkflowSection({ h }: { h: WorkflowHealth }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4">
      <SectionHeader icon={GitBranch} title="Workflow Runs" sub="last 24h" />
      <div className="grid grid-cols-4 gap-2">
        <StatCard label="Running"   value={h.running}           color="blue"  />
        <StatCard label="Awaiting"  value={h.awaiting_approval} color="amber" />
        <StatCard label="Completed" value={h.completed}         color="green" />
        <StatCard label="Failed"    value={h.failed}            color="red"   />
      </div>
    </div>
  );
}

// ── CU section ────────────────────────────────────────────────────────────────

function CuSection({ h }: { h: CuHealth }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4">
      <SectionHeader icon={Cpu} title="Computer-Use Jobs" sub="last 24h" />
      <div className="grid grid-cols-4 gap-2">
        <StatCard label="Pending"   value={h.pending}   color="slate" />
        <StatCard label="Running"   value={h.running}   color="blue"  />
        <StatCard label="Completed" value={h.completed} color="green" />
        <StatCard label="Failed"    value={h.failed}    color="red"   />
      </div>
    </div>
  );
}

// ── Ingestion section ─────────────────────────────────────────────────────────

function IngestionSection({ h }: { h: IngestionHealth }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4">
      <SectionHeader icon={HardDrive} title="File Ingestion" sub="last 24h" />
      <div className="grid grid-cols-4 gap-2">
        <StatCard label="Pending"    value={h.pending}    color="slate" />
        <StatCard label="Processing" value={h.processing} color="blue"  />
        <StatCard label="Done"       value={h.done}       color="green" />
        <StatCard label="Error"      value={h.error}      color="red"   />
      </div>
    </div>
  );
}

// ── Workflow run status badge ──────────────────────────────────────────────────

function RunStatusBadge({ status }: { status: string }) {
  const styles: Record<string, { cls: string; icon: React.ReactNode }> = {
    running:           { cls: 'bg-blue-100 text-blue-700',    icon: <Clock size={10} /> },
    completed:         { cls: 'bg-emerald-100 text-emerald-700', icon: <CheckCircle2 size={10} /> },
    failed:            { cls: 'bg-red-100 text-red-700',      icon: <XCircle size={10} /> },
    awaiting_approval: { cls: 'bg-amber-100 text-amber-700',  icon: <AlertTriangle size={10} /> },
    cancelled:         { cls: 'bg-slate-100 text-slate-500',  icon: <XCircle size={10} /> },
  };
  const s = styles[status] ?? { cls: 'bg-slate-100 text-slate-500', icon: null };
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${s.cls}`}>
      {s.icon}{status.replace(/_/g, ' ')}
    </span>
  );
}

// ── Workflow runs table ───────────────────────────────────────────────────────

function WorkflowRunsTable() {
  const [data, setData]         = useState<WorkflowRunsResponse | null>(null);
  const [loading, setLoading]   = useState(true);
  const [page, setPage]         = useState(1);
  const [statusFilter, setStatusFilter] = useState('');

  const load = useCallback(async (p: number, status: string) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p), page_size: '20' });
      if (status) params.set('status', status);
      const res = await api.get<WorkflowRunsResponse>(`/api/admin/workflow-runs?${params}`);
      setData(res);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(page, statusFilter); }, [page, statusFilter, load]);

  return (
    <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
          <GitBranch size={14} className="text-slate-400" /> Recent Workflow Runs
        </div>
        <select
          className="text-xs border border-slate-200 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={statusFilter}
          onChange={e => { setPage(1); setStatusFilter(e.target.value); }}
        >
          <option value="">All statuses</option>
          <option value="running">Running</option>
          <option value="awaiting_approval">Awaiting Approval</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-10 text-slate-400 text-sm">Loading…</div>
      ) : !data || data.runs.length === 0 ? (
        <div className="flex items-center justify-center py-10 text-slate-400 text-sm">
          No workflow runs found
        </div>
      ) : (
        <>
          <table className="w-full">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-100">
                {['Run ID', 'Workflow', 'Status', 'Created', 'Updated'].map(h => (
                  <th key={h} className="text-left py-2 px-4 text-xs font-medium text-slate-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.runs.map(run => (
                <tr key={run.id} className="border-b border-slate-100 hover:bg-slate-50">
                  <td className="py-2.5 px-4 font-mono text-xs text-slate-500">{run.id.slice(0, 8)}…</td>
                  <td className="py-2.5 px-4 text-sm text-slate-700">{run.workflow_name}</td>
                  <td className="py-2.5 px-4"><RunStatusBadge status={run.status} /></td>
                  <td className="py-2.5 px-4 text-xs text-slate-400">{relativeTime(run.created_at)}</td>
                  <td className="py-2.5 px-4 text-xs text-slate-400">{relativeTime(run.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex items-center justify-between px-4 py-2.5 border-t border-slate-100 text-xs text-slate-500">
            <span>{data.total} total runs</span>
            <div className="flex items-center gap-1">
              <button
                disabled={page <= 1}
                onClick={() => setPage(p => p - 1)}
                className="p-1 rounded hover:bg-slate-100 disabled:opacity-40"
              >
                <ChevronLeft size={13} />
              </button>
              <span>Page {data.page} of {data.pages}</span>
              <button
                disabled={page >= data.pages}
                onClick={() => setPage(p => p + 1)}
                className="p-1 rounded hover:bg-slate-100 disabled:opacity-40"
              >
                <ChevronRight size={13} />
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function AdminDiag() {
  const [health, setHealth]   = useState<SystemHealth | null>(null);
  const [loading, setLoading] = useState(true);

  const loadHealth = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get<SystemHealth>('/api/admin/system-health');
      setHealth(res);
    } catch {
      setHealth(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadHealth(); }, [loadHealth]);

  return (
    <div className="h-full overflow-auto bg-slate-50">
      <div className="max-w-5xl mx-auto p-6 space-y-4">

        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-slate-700 rounded-lg flex items-center justify-center">
              <Activity size={15} className="text-white" />
            </div>
            <div>
              <h1 className="text-lg font-semibold text-slate-800">Admin Diagnostics</h1>
              <p className="text-xs text-slate-500">
                {health ? `Updated ${relativeTime(health.generated_at)}` : 'System health overview'}
              </p>
            </div>
          </div>
          <button
            onClick={loadHealth}
            disabled={loading}
            className="flex items-center gap-1.5 text-sm text-slate-600 hover:text-slate-800 px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-white transition-colors disabled:opacity-50"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>

        {/* Audit event count */}
        {health && (
          <div className="bg-white rounded-xl border border-slate-200 p-4 flex items-center gap-3">
            <div className="w-10 h-10 bg-violet-50 rounded-lg flex items-center justify-center">
              <Activity size={18} className="text-violet-600" />
            </div>
            <div>
              <div className="text-2xl font-bold text-slate-800">{health.audit_events_24h}</div>
              <div className="text-xs text-slate-500">Audit events in last 24 hours</div>
            </div>
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-16 text-slate-400 text-sm">Loading…</div>
        ) : !health ? (
          <div className="bg-white rounded-xl border border-slate-200 flex items-center justify-center py-16 text-slate-400 text-sm">
            Could not load system health
          </div>
        ) : (
          <>
            <ConnectorSection h={health.connectors} />
            <WorkflowSection  h={health.workflow_runs} />
            <CuSection        h={health.computer_use} />
            <IngestionSection h={health.ingestion} />
          </>
        )}

        {/* Workflow runs table always shown */}
        <WorkflowRunsTable />

      </div>
    </div>
  );
}
