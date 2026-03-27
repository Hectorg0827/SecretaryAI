import { useEffect, useState } from 'react';
import {
  X, TrendingUp, TrendingDown, AlertTriangle, Clock,
  Mail, FileText, StickyNote, DollarSign, Calendar,
  Phone, Building2, ChevronRight, ExternalLink
} from 'lucide-react';
import { InlineAssistant } from '../layout/InlineAssistant';
import { api, Account } from '../../lib/api';
import { useAuth } from '../../hooks/useAuth';

interface Order {
  id: string;
  invoice_number?: string;
  invoice_date: string;
  total_amount: number;
  status?: string;
  items?: { name: string; qty: number; price: number }[];
}

interface Props {
  account: Account | null;
  onClose: () => void;
}

const HEALTH_CONFIG = {
  healthy:  { color: 'text-emerald-600', bg: 'bg-emerald-50', border: 'border-emerald-200', Icon: TrendingUp,    label: 'Healthy'  },
  slowing:  { color: 'text-amber-600',   bg: 'bg-amber-50',   border: 'border-amber-200',   Icon: TrendingDown,  label: 'Slowing'  },
  at_risk:  { color: 'text-orange-600',  bg: 'bg-orange-50',  border: 'border-orange-200',  Icon: AlertTriangle, label: 'At Risk'  },
  dormant:  { color: 'text-red-600',     bg: 'bg-red-50',     border: 'border-red-200',     Icon: Clock,         label: 'Dormant'  },
  unknown:  { color: 'text-slate-500',   bg: 'bg-slate-50',   border: 'border-slate-200',   Icon: Building2,     label: 'Unknown'  },
};

type Tab = 'overview' | 'orders' | 'notes';

export function AccountDetail({ account, onClose }: Props) {
  const { canTriggerActions } = useAuth();
  const [tab, setTab]         = useState<Tab>('overview');
  const [orders, setOrders]   = useState<Order[]>([]);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [note, setNote]       = useState('');
  const [notes, setNotes]     = useState<{ id: string; text: string; created_at: string }[]>([]);
  const [savingNote, setSavingNote] = useState(false);
  const [draftingEmail, setDraftingEmail] = useState(false);

  useEffect(() => {
    if (!account) return;
    setTab('overview');
    setOrders([]);
    setNotes([]);
  }, [account?.id]);

  useEffect(() => {
    if (tab === 'orders' && account && orders.length === 0) {
      loadOrders();
    }
    if (tab === 'notes' && account) {
      loadNotes();
    }
  }, [tab, account]);

  const loadOrders = async () => {
    if (!account) return;
    setOrdersLoading(true);
    try {
      const res = await api.get<{ orders: Order[] }>(`/api/accounts/${account.id}/orders`);
      setOrders(res.orders ?? []);
    } catch {
      setOrders([]);
    } finally {
      setOrdersLoading(false);
    }
  };

  const loadNotes = async () => {
    try {
      const res = await api.get<{ notes: { id: string; text: string; created_at: string }[] }>(
        `/api/dashboard/notes?account_id=${account?.id}`
      );
      setNotes(res.notes ?? []);
    } catch {
      setNotes([]);
    }
  };

  const saveNote = async () => {
    if (!note.trim() || !account) return;
    setSavingNote(true);
    try {
      await api.post('/api/dashboard/notes', { text: note.trim(), account_name: account.name });
      setNote('');
      loadNotes();
    } finally {
      setSavingNote(false);
    }
  };

  const draftEmail = async () => {
    if (!account) return;
    setDraftingEmail(true);
    try {
      await api.post('/api/dashboard/emails/draft-reply', {
        email_id: null,
        to: account.email,
        context: `Follow-up for account: ${account.name}`,
      });
    } finally {
      setDraftingEmail(false);
    }
  };

  if (!account) return null;

  const health = HEALTH_CONFIG[account.health_status] ?? HEALTH_CONFIG.unknown;
  const { Icon: HealthIcon } = health;

  const fmt = (n?: number) =>
    n != null ? `$${n.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}` : '—';

  const fmtDate = (d?: string | null) =>
    d ? new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—';

  return (
    <div className="flex flex-col h-full bg-white border-l border-slate-200 overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between px-5 pt-5 pb-4 border-b border-slate-100">
        <div className="flex-1 min-w-0 mr-3">
          <div className="flex items-center gap-2 mb-1">
            <h2 className="text-base font-semibold text-slate-900 truncate">{account.name}</h2>
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${health.bg} ${health.color} ${health.border}`}>
              <HealthIcon className="w-3 h-3" />
              {health.label}
            </span>
          </div>
          <div className="flex items-center gap-3 text-xs text-slate-500">
            {account.email && (
              <span className="flex items-center gap-1">
                <Mail className="w-3 h-3" />{account.email}
              </span>
            )}
            {account.state && (
              <span className="flex items-center gap-1">
                <Building2 className="w-3 h-3" />{account.state}
              </span>
            )}
          </div>
        </div>
        <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-colors">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-3 divide-x divide-slate-100 border-b border-slate-100">
        {[
          { label: 'Balance',    value: fmt(account.current_balance),  Icon: DollarSign },
          { label: 'Avg Order',  value: fmt(account.avg_order_value),   Icon: TrendingUp },
          { label: 'Last Order', value: fmtDate(account.last_order_date), Icon: Calendar  },
        ].map(({ label, value, Icon }) => (
          <div key={label} className="px-4 py-3 text-center">
            <Icon className="w-3.5 h-3.5 text-slate-400 mx-auto mb-0.5" />
            <p className="text-sm font-semibold text-slate-800">{value}</p>
            <p className="text-xs text-slate-400">{label}</p>
          </div>
        ))}
      </div>

      {/* Quick actions */}
      {canTriggerActions && (
        <div className="flex gap-2 px-4 py-3 border-b border-slate-100">
          <button
            onClick={draftEmail}
            disabled={draftingEmail}
            className="flex-1 flex items-center justify-center gap-1.5 text-xs font-medium px-3 py-2 rounded-lg bg-blue-50 text-blue-700 hover:bg-blue-100 transition-colors disabled:opacity-50"
          >
            <Mail className="w-3.5 h-3.5" />
            {draftingEmail ? 'Drafting…' : 'Draft Email'}
          </button>
          <button
            onClick={() => setTab('notes')}
            className="flex-1 flex items-center justify-center gap-1.5 text-xs font-medium px-3 py-2 rounded-lg bg-slate-50 text-slate-700 hover:bg-slate-100 transition-colors"
          >
            <StickyNote className="w-3.5 h-3.5" />
            Add Note
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex border-b border-slate-100 px-4">
        {(['overview', 'orders', 'notes'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-2.5 text-xs font-medium capitalize border-b-2 -mb-px transition-colors ${
              tab === t
                ? 'border-blue-600 text-blue-700'
                : 'border-transparent text-slate-500 hover:text-slate-700'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
        {/* Overview tab */}
        {tab === 'overview' && (
          <div className="p-4 space-y-4">
            {/* Health score bar */}
            {account.health_score != null && (
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-slate-500">Health score</span>
                  <span className={`font-medium ${health.color}`}>{account.health_score}/100</span>
                </div>
                <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      account.health_score >= 75 ? 'bg-emerald-500' :
                      account.health_score >= 50 ? 'bg-amber-400' :
                      account.health_score >= 25 ? 'bg-orange-500' : 'bg-red-500'
                    }`}
                    style={{ width: `${account.health_score}%` }}
                  />
                </div>
              </div>
            )}

            {/* Details */}
            <div className="space-y-2">
              {[
                { label: 'Phone',        value: account.phone },
                { label: 'Rep',          value: account.assigned_rep },
                { label: 'Outstanding',  value: fmt(account.current_balance) },
              ].filter((r) => r.value).map(({ label, value }) => (
                <div key={label} className="flex justify-between text-xs">
                  <span className="text-slate-400">{label}</span>
                  <span className="text-slate-700 font-medium">{value}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Orders tab */}
        {tab === 'orders' && (
          <div className="p-4">
            {ordersLoading ? (
              <div className="space-y-2">
                {[...Array(4)].map((_, i) => (
                  <div key={i} className="h-12 bg-slate-100 rounded-lg animate-pulse" />
                ))}
              </div>
            ) : orders.length === 0 ? (
              <p className="text-xs text-slate-400 text-center py-8">No orders found</p>
            ) : (
              <div className="space-y-2">
                {orders.map((o) => (
                  <div key={o.id} className="flex items-center justify-between p-3 rounded-lg border border-slate-100 hover:bg-slate-50 transition-colors">
                    <div>
                      <p className="text-xs font-medium text-slate-700">
                        {o.invoice_number ?? o.id.slice(0, 8)}
                      </p>
                      <p className="text-xs text-slate-400">{fmtDate(o.invoice_date)}</p>
                    </div>
                    <div className="text-right">
                      <p className="text-xs font-semibold text-slate-800">{fmt(o.total_amount)}</p>
                      {o.status && (
                        <p className="text-xs text-slate-400 capitalize">{o.status}</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Notes tab */}
        {tab === 'notes' && (
          <div className="p-4 space-y-3">
            {canTriggerActions && (
              <div className="space-y-2">
                <textarea
                  rows={3}
                  placeholder="Add a note about this account..."
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  className="w-full text-xs px-3 py-2 border border-slate-200 rounded-lg resize-none outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100"
                />
                <button
                  onClick={saveNote}
                  disabled={savingNote || !note.trim()}
                  className="w-full text-xs font-medium px-3 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
                >
                  {savingNote ? 'Saving…' : 'Save Note'}
                </button>
              </div>
            )}
            {notes.length === 0 ? (
              <p className="text-xs text-slate-400 text-center py-4">No notes yet</p>
            ) : (
              <div className="space-y-2">
                {notes.map((n) => (
                  <div key={n.id} className="p-3 rounded-lg bg-amber-50 border border-amber-100">
                    <p className="text-xs text-slate-700">{n.text}</p>
                    <p className="text-xs text-slate-400 mt-1">{fmtDate(n.created_at)}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Inline AI */}
      <div className="border-t border-slate-100">
        <InlineAssistant
          context={{
            type: 'account',
            label: account.name,
            data: {
              name:          account.name,
              health_status: account.health_status,
              health_score:  account.health_score,
              last_order:    account.last_order_date,
              balance:       account.current_balance,
              avg_order:     account.avg_order_value,
            },
          }}
          initialSuggestion={
            account.health_status === 'dormant'
              ? `${account.name} has gone dormant. Want me to draft a re-engagement email?`
              : account.health_status === 'at_risk'
              ? `${account.name} is showing signs of slowing. Want me to flag this for follow-up?`
              : undefined
          }
        />
      </div>
    </div>
  );
}
