import { useEffect, useState } from 'react';
import {
  X, TrendingUp, TrendingDown, AlertTriangle, Clock,
  Mail, StickyNote, DollarSign, Calendar, Building2,
} from 'lucide-react';
import { InlineAssistant } from '../layout/InlineAssistant';
import { api, Account } from '../../lib/api';
import { useAuth } from '../../hooks/useAuth';

/** One invoice from GET /api/accounts/{id}/orders. */
export interface AccountOrder {
  id: string;
  invoice_date: string;
  due_date: string | null;
  total_amount: number;
  balance_due: number | null;
  status: string;
}

export interface AccountOrdersResponse {
  account_id: string;
  orders: AccountOrder[];
  count: number;
  total: number;
  truncated: boolean;
  history_window_days: number;
  unmatched_invoices: number;
}

type OrdersState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: AccountOrdersResponse };

// The account response says whether an average was observed; a missing
// average must not be shown as $0.
type AccountWithQuality = Account & { avg_order_value_observed?: boolean };

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

const fmt = (n?: number | null) =>
  n != null ? `$${n.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}` : '—';

const fmtMoney = (n?: number | null) =>
  n != null ? `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—';

/** Date-only strings (YYYY-MM-DD) are calendar days, not UTC midnight. */
function fmtDate(d?: string | null): string {
  if (!d) return '—';
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d);
  const parsed = dateOnly
    ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
    : new Date(d);
  if (Number.isNaN(parsed.getTime())) return '—';
  return parsed.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function isOrdersResponse(data: unknown): data is AccountOrdersResponse {
  const body = data as Partial<AccountOrdersResponse> | null;
  return !!body && Array.isArray(body.orders) && typeof body.total === 'number'
    && typeof body.history_window_days === 'number';
}

/** Invoices for one account. A failed read is shown as an error, never as "no orders". */
export function AccountOrders({ accountId }: { accountId: string }) {
  const [state, setState] = useState<OrdersState>({ status: 'loading' });
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let current = true;
    setState({ status: 'loading' });
    api.get<AccountOrdersResponse>(`/api/accounts/${encodeURIComponent(accountId)}/orders`)
      .then((data) => {
        if (!isOrdersResponse(data)) throw new Error('The order history response was incomplete.');
        if (current) setState({ status: 'ready', data });
      })
      .catch((err: unknown) => {
        if (!current) return;
        const message = err instanceof Error && err.message ? err.message : 'The server did not answer.';
        setState({ status: 'error', message });
      });
    return () => { current = false; };
  }, [accountId, reload]);

  if (state.status === 'loading') {
    return (
      <div className="space-y-2" role="status" aria-label="Loading orders">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-12 bg-slate-100 rounded-lg animate-pulse" />
        ))}
      </div>
    );
  }

  if (state.status === 'error') {
    return (
      <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700 space-y-2">
        <p className="flex items-start gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span>Orders could not be loaded: {state.message}</span>
        </p>
        <button
          type="button"
          onClick={() => setReload((n) => n + 1)}
          className="font-medium text-red-700 underline hover:text-red-800"
        >
          Try again
        </button>
      </div>
    );
  }

  const { orders, total, truncated, history_window_days: days, unmatched_invoices: unmatched } = state.data;
  return (
    <div className="space-y-2">
      <p className="text-xs text-slate-500">
        {total === 0
          ? `No invoices for this account in the last ${days} days.`
          : truncated
            ? `Showing the newest ${orders.length} of ${total} invoices from the last ${days} days.`
            : `${total} invoice${total === 1 ? '' : 's'} from the last ${days} days.`}
      </p>
      {unmatched > 0 && (
        <p className="rounded-lg border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800">
          {unmatched} invoice{unmatched === 1 ? ' has' : 's have'} no customer attached in QuickBooks, so this list may be missing some orders.
        </p>
      )}
      {orders.map((o) => (
        <div key={o.id} className="flex items-center justify-between p-3 rounded-lg border border-slate-100 hover:bg-slate-50 transition-colors">
          <div>
            <p className="text-xs font-medium text-slate-700">QuickBooks ref {o.id}</p>
            <p className="text-xs text-slate-400">{fmtDate(o.invoice_date)}</p>
          </div>
          <div className="text-right">
            <p className="text-xs font-semibold text-slate-800">{fmtMoney(o.total_amount)}</p>
            {o.status && <p className="text-xs text-slate-400 capitalize">{o.status}</p>}
            {o.status === 'open' && o.balance_due != null && (
              <p className="text-xs text-slate-500">{fmtMoney(o.balance_due)} still owed</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

export function AccountDetail({ account, onClose }: Props) {
  const { canTriggerActions } = useAuth();
  const [tab, setTab]         = useState<Tab>('overview');
  const [note, setNote]       = useState('');
  const [notes, setNotes]     = useState<{ id: string; text: string; created_at: string }[]>([]);
  const [savingNote, setSavingNote] = useState(false);
  const [draftingEmail, setDraftingEmail] = useState(false);

  const accountId = account?.id;

  useEffect(() => {
    setTab('overview');
    setNotes([]);
  }, [accountId]);

  useEffect(() => {
    if (tab === 'notes' && account) {
      loadNotes();
    }
  }, [tab, account]);

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

  const avgOrderObserved = (account as AccountWithQuality).avg_order_value_observed !== false;

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
          { label: 'Avg Order',  value: avgOrderObserved ? fmt(account.avg_order_value) : '—', Icon: TrendingUp },
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
            <AccountOrders key={account.id} accountId={account.id} />
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
              avg_order:     avgOrderObserved ? account.avg_order_value : null,
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
