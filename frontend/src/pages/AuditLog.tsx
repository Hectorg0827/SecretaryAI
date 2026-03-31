import React, { useEffect, useState, useCallback } from 'react';
import { Shield, RefreshCw, ChevronLeft, ChevronRight, Filter, X } from 'lucide-react';
import { api } from '../lib/api';

// ── Types ─────────────────────────────────────────────────────────────────────

interface AuditEvent {
  id: string;
  company_id: string;
  event_type: string;
  actor_id?: string;
  action_class?: string;
  capability?: string;
  path_used?: string;
  policy_rule_id?: string;
  approved_by?: string;
  metadata?: Record<string, unknown>;
  created_at: string;
}

interface AuditLogResponse {
  events: AuditEvent[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch {
    return iso;
  }
}

const EVENT_TYPE_STYLES: Record<string, string> = {
  draft_approved:     'bg-emerald-100 text-emerald-700',
  draft_rejected:     'bg-red-100    text-red-700',
  user_login:         'bg-blue-100   text-blue-700',
  company_registered: 'bg-purple-100 text-purple-700',
  policy_blocked:     'bg-orange-100 text-orange-700',
  workflow_failed:    'bg-red-100    text-red-700',
  workflow_cancelled: 'bg-slate-100  text-slate-600',
};

function EventTypeBadge({ type }: { type: string }) {
  const style = EVENT_TYPE_STYLES[type] ?? 'bg-slate-100 text-slate-600';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${style}`}>
      {type.replace(/_/g, ' ')}
    </span>
  );
}

// ── Filter bar ────────────────────────────────────────────────────────────────

interface Filters {
  event_type: string;
  actor_id: string;
  since: string;
  until: string;
}

function FilterBar({
  filters,
  onChange,
  onClear,
}: {
  filters: Filters;
  onChange: (k: keyof Filters, v: string) => void;
  onClear: () => void;
}) {
  const hasFilters = Object.values(filters).some(Boolean);
  return (
    <div className="flex flex-wrap gap-2 items-end">
      <div>
        <label className="block text-xs text-slate-500 mb-1">Event type</label>
        <input
          className="text-sm border border-slate-200 rounded px-2 py-1.5 w-40 focus:outline-none focus:ring-2 focus:ring-blue-500"
          placeholder="e.g. draft_approved"
          value={filters.event_type}
          onChange={e => onChange('event_type', e.target.value)}
        />
      </div>
      <div>
        <label className="block text-xs text-slate-500 mb-1">Actor ID</label>
        <input
          className="text-sm border border-slate-200 rounded px-2 py-1.5 w-48 focus:outline-none focus:ring-2 focus:ring-blue-500"
          placeholder="user UUID"
          value={filters.actor_id}
          onChange={e => onChange('actor_id', e.target.value)}
        />
      </div>
      <div>
        <label className="block text-xs text-slate-500 mb-1">Since</label>
        <input
          type="date"
          className="text-sm border border-slate-200 rounded px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={filters.since}
          onChange={e => onChange('since', e.target.value)}
        />
      </div>
      <div>
        <label className="block text-xs text-slate-500 mb-1">Until</label>
        <input
          type="date"
          className="text-sm border border-slate-200 rounded px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={filters.until}
          onChange={e => onChange('until', e.target.value)}
        />
      </div>
      {hasFilters && (
        <button
          onClick={onClear}
          className="flex items-center gap-1 text-xs text-slate-500 hover:text-red-500 transition-colors px-2 py-1.5"
        >
          <X size={12} /> Clear
        </button>
      )}
    </div>
  );
}

// ── Event row ─────────────────────────────────────────────────────────────────

function EventRow({ event }: { event: AuditEvent }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = event.metadata && Object.keys(event.metadata).length > 0;

  return (
    <>
      <tr
        className={`border-b border-slate-100 hover:bg-slate-50 ${hasDetail ? 'cursor-pointer' : ''}`}
        onClick={() => hasDetail && setExpanded(x => !x)}
      >
        <td className="py-2.5 px-4 text-xs text-slate-500 whitespace-nowrap font-mono">
          {formatDateTime(event.created_at)}
        </td>
        <td className="py-2.5 px-4">
          <EventTypeBadge type={event.event_type} />
        </td>
        <td className="py-2.5 px-4 text-xs text-slate-600 font-mono truncate max-w-[160px]">
          {event.actor_id ? event.actor_id.slice(0, 8) + '…' : '—'}
        </td>
        <td className="py-2.5 px-4 text-xs text-slate-500">
          {event.action_class ?? '—'}
        </td>
        <td className="py-2.5 px-4 text-xs text-slate-500">
          {event.capability ?? '—'}
        </td>
        <td className="py-2.5 px-4 text-xs text-slate-400 font-mono truncate max-w-[120px]">
          {event.approved_by ? event.approved_by.slice(0, 8) + '…' : event.policy_rule_id ?? '—'}
        </td>
      </tr>
      {expanded && hasDetail && (
        <tr className="bg-slate-50 border-b border-slate-100">
          <td colSpan={6} className="px-4 py-3">
            <pre className="text-xs text-slate-600 font-mono whitespace-pre-wrap break-all">
              {JSON.stringify(event.metadata, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}

// ── Pagination ────────────────────────────────────────────────────────────────

function Pagination({
  page, pages, total, pageSize, onPage,
}: {
  page: number; pages: number; total: number; pageSize: number; onPage: (p: number) => void;
}) {
  const start = (page - 1) * pageSize + 1;
  const end   = Math.min(page * pageSize, total);
  return (
    <div className="flex items-center justify-between text-xs text-slate-500 pt-2">
      <span>{total > 0 ? `${start}–${end} of ${total} events` : 'No events'}</span>
      <div className="flex items-center gap-1">
        <button
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          className="p-1 rounded hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <ChevronLeft size={14} />
        </button>
        <span className="px-2">Page {page} of {pages}</span>
        <button
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
          className="p-1 rounded hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <ChevronRight size={14} />
        </button>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

const EMPTY_FILTERS: Filters = { event_type: '', actor_id: '', since: '', until: '' };

export function AuditLog() {
  const [data, setData]       = useState<AuditLogResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage]       = useState(1);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);

  const load = useCallback(async (p: number, f: Filters) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p), page_size: '50' });
      if (f.event_type) params.set('event_type', f.event_type);
      if (f.actor_id)   params.set('actor_id',   f.actor_id);
      if (f.since)      params.set('since',       f.since);
      if (f.until)      params.set('until',       f.until);
      const res = await api.get<AuditLogResponse>(`/api/admin/audit-log?${params}`);
      setData(res);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(page, filters); }, [page, filters, load]);

  const handleFilterChange = (k: keyof Filters, v: string) => {
    setPage(1);
    setFilters(prev => ({ ...prev, [k]: v }));
  };

  const handleClearFilters = () => {
    setPage(1);
    setFilters(EMPTY_FILTERS);
  };

  return (
    <div className="h-full overflow-auto bg-slate-50">
      <div className="max-w-6xl mx-auto p-6 space-y-4">

        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-violet-500 rounded-lg flex items-center justify-center">
              <Shield size={15} className="text-white" />
            </div>
            <div>
              <h1 className="text-lg font-semibold text-slate-800">Audit Log</h1>
              <p className="text-xs text-slate-500">Immutable record of all privileged actions</p>
            </div>
          </div>
          <button
            onClick={() => load(page, filters)}
            disabled={loading}
            className="flex items-center gap-1.5 text-sm text-slate-600 hover:text-slate-800 px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-white transition-colors disabled:opacity-50"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>

        {/* Filter bar */}
        <div className="bg-white rounded-xl border border-slate-200 p-4">
          <div className="flex items-center gap-2 mb-3 text-sm font-medium text-slate-600">
            <Filter size={13} /> Filters
          </div>
          <FilterBar filters={filters} onChange={handleFilterChange} onClear={handleClearFilters} />
        </div>

        {/* Table */}
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-slate-400 text-sm">
              Loading…
            </div>
          ) : !data || data.events.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-slate-400">
              <Shield size={32} className="mb-3 opacity-30" />
              <p className="text-sm">No audit events found</p>
            </div>
          ) : (
            <>
              <table className="w-full">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50">
                    {['Timestamp', 'Event', 'Actor', 'Class', 'Capability', 'Rule / Approver'].map(h => (
                      <th key={h} className="text-left py-2.5 px-4 text-xs font-medium text-slate-500">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.events.map(ev => <EventRow key={ev.id} event={ev} />)}
                </tbody>
              </table>
              <div className="px-4 pb-3 pt-1 border-t border-slate-100">
                <Pagination
                  page={data.page}
                  pages={data.pages}
                  total={data.total}
                  pageSize={data.page_size}
                  onPage={p => { setPage(p); load(p, filters); }}
                />
              </div>
            </>
          )}
        </div>

      </div>
    </div>
  );
}
