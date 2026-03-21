import { useEffect, useState } from 'react';
import {
  Mail, AlertTriangle, CheckCircle2, TrendingDown,
  RefreshCw, Check, X, ChevronRight,
  Inbox as InboxIcon, Package, Clock
} from 'lucide-react';
import toast from 'react-hot-toast';
import { useInboxStore, InboxItem, ItemType } from '../stores/inboxStore';
import { InlineAssistant } from '../components/layout/InlineAssistant';
import { api } from '../lib/api';
import { useAuth } from '../hooks/useAuth';

// ── Item type config ──────────────────────────────────────────────────────────
const TYPE_CONFIG: Record<ItemType, { Icon: React.ElementType; color: string; bg: string; label: string }> = {
  email:        { Icon: Mail,          color: 'text-blue-600',    bg: 'bg-blue-50',    label: 'Email'    },
  alert:        { Icon: Package,       color: 'text-amber-600',   bg: 'bg-amber-50',   label: 'Alert'    },
  approval:     { Icon: CheckCircle2,  color: 'text-emerald-600', bg: 'bg-emerald-50', label: 'Approval' },
  health_event: { Icon: TrendingDown,  color: 'text-orange-600',  bg: 'bg-orange-50',  label: 'Account'  },
};

const PRIORITY_DOT: Record<string, string> = {
  high:   'bg-red-500',
  medium: 'bg-amber-400',
  low:    'bg-slate-300',
};

const FILTERS: { key: 'all' | ItemType; label: string }[] = [
  { key: 'all',         label: 'All'       },
  { key: 'email',       label: 'Emails'    },
  { key: 'alert',       label: 'Alerts'    },
  { key: 'approval',    label: 'Approvals' },
  { key: 'health_event',label: 'Accounts'  },
];

function relativeTime(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const mins  = Math.floor(diff / 60_000);
  if (mins < 1)   return 'just now';
  if (mins < 60)  return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24)   return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

// ── Feed item row ─────────────────────────────────────────────────────────────
function FeedRow({ item, selected, onClick }: { item: InboxItem; selected: boolean; onClick: () => void }) {
  const cfg = TYPE_CONFIG[item.type];
  const { Icon } = cfg;

  return (
    <button
      onClick={onClick}
      className={`w-full flex items-start gap-3 px-4 py-3.5 border-b border-slate-100 text-left transition-colors
        ${selected ? 'bg-blue-50 border-l-2 border-l-blue-500' : 'hover:bg-slate-50 border-l-2 border-l-transparent'}
        ${!item.is_read ? 'bg-white' : 'opacity-75'}`}
    >
      {/* Type icon */}
      <div className={`mt-0.5 flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center ${cfg.bg}`}>
        <Icon className={`w-3.5 h-3.5 ${cfg.color}`} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 mb-0.5">
          {!item.is_read && (
            <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${PRIORITY_DOT[item.priority]}`} />
          )}
          <span className={`text-sm truncate ${!item.is_read ? 'font-semibold text-slate-900' : 'font-medium text-slate-600'}`}>
            {item.title}
          </span>
        </div>
        <p className="text-xs text-slate-500 truncate">{item.subtitle}</p>
      </div>

      {/* Time + chevron */}
      <div className="flex flex-col items-end gap-1 flex-shrink-0">
        <span className="text-xs text-slate-400">{relativeTime(item.timestamp)}</span>
        <ChevronRight className="w-3 h-3 text-slate-300" />
      </div>
    </button>
  );
}

// ── Detail panel ──────────────────────────────────────────────────────────────
function DetailPanel({ item }: { item: InboxItem }) {
  const { canApprove, canTriggerActions } = useAuth();
  const { markRead } = useInboxStore();
  const [draftReply, setDraftReply]   = useState('');
  const [drafting, setDrafting]       = useState(false);
  const [sending, setSending]         = useState(false);
  const [approving, setApproving]     = useState(false);
  const [rejecting, setRejecting]     = useState(false);
  const [done, setDone]               = useState(false);

  const payload = item.payload as Record<string, unknown>;

  const handleDraftReply = async () => {
    setDrafting(true);
    try {
      const res = await api.post<{ draft: string }>('/api/dashboard/emails/draft-reply', {
        email_id: payload.id ?? payload.thread_id,
        context:  `${item.title}: ${item.subtitle}`,
      });
      setDraftReply(res.draft ?? '');
    } catch {
      toast.error('Failed to draft reply');
    } finally {
      setDrafting(false);
    }
  };

  const handleSendReply = async () => {
    setSending(true);
    try {
      await api.post('/api/dashboard/emails/send-reply', {
        email_id: payload.id ?? payload.thread_id,
        subject:  `Re: ${item.subtitle}`,
        body:     draftReply,
      });
      toast.success('Reply sent');
      markRead(item.id);
      setDone(true);
    } catch {
      toast.error('Failed to send reply');
    } finally {
      setSending(false);
    }
  };

  const handleApprove = async () => {
    setApproving(true);
    try {
      await api.post(`/api/actions/approve/${payload.draft_id}`, {});
      toast.success('Approved');
      markRead(item.id);
      setDone(true);
    } catch {
      toast.error('Approval failed');
    } finally {
      setApproving(false);
    }
  };

  const handleReject = async () => {
    setRejecting(true);
    try {
      await api.post(`/api/actions/reject/${payload.draft_id}`, { reason: 'Rejected from inbox' });
      toast.success('Rejected');
      markRead(item.id);
      setDone(true);
    } catch {
      toast.error('Rejection failed');
    } finally {
      setRejecting(false);
    }
  };

  const cfg = TYPE_CONFIG[item.type];
  const { Icon } = cfg;

  if (done) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-slate-400">
        <Check className="w-10 h-10 text-emerald-500" />
        <p className="text-sm font-medium text-slate-600">Done</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Detail header */}
      <div className="px-6 py-5 border-b border-slate-100">
        <div className="flex items-start gap-3">
          <div className={`w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 ${cfg.bg}`}>
            <Icon className={`w-4.5 h-4.5 ${cfg.color}`} />
          </div>
          <div>
            <h2 className="text-base font-semibold text-slate-900 leading-tight">{item.title}</h2>
            <p className="text-sm text-slate-500 mt-0.5">{item.subtitle}</p>
            <p className="text-xs text-slate-400 mt-1">{relativeTime(item.timestamp)}</p>
          </div>
        </div>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">

        {/* EMAIL detail */}
        {item.type === 'email' && (
          <>
            {payload.ai_summary && (
              <div className="bg-blue-50 border border-blue-100 rounded-xl p-4">
                <p className="text-xs font-semibold text-blue-700 mb-1 uppercase tracking-wide">AI Summary</p>
                <p className="text-sm text-slate-700">{String(payload.ai_summary)}</p>
                {payload.ai_action_needed && (
                  <p className="mt-2 text-xs text-red-600 font-medium">⚡ Action needed</p>
                )}
              </div>
            )}

            {canTriggerActions && (
              <div>
                {draftReply ? (
                  <div className="space-y-3">
                    <label className="text-xs font-medium text-slate-600 uppercase tracking-wide">Draft Reply</label>
                    <textarea
                      rows={6}
                      value={draftReply}
                      onChange={(e) => setDraftReply(e.target.value)}
                      className="w-full text-sm px-4 py-3 border border-slate-200 rounded-xl resize-none outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50"
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={handleSendReply}
                        disabled={sending}
                        className="flex-1 py-2 text-sm font-medium rounded-xl bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
                      >
                        {sending ? 'Sending…' : 'Send Reply'}
                      </button>
                      <button
                        onClick={() => setDraftReply('')}
                        className="px-4 py-2 text-sm font-medium rounded-xl bg-slate-100 text-slate-600 hover:bg-slate-200 transition-colors"
                      >
                        Discard
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    onClick={handleDraftReply}
                    disabled={drafting}
                    className="w-full py-2.5 text-sm font-medium rounded-xl border border-blue-200 text-blue-700 bg-blue-50 hover:bg-blue-100 transition-colors disabled:opacity-50"
                  >
                    {drafting ? 'Drafting…' : '✦ Draft Reply with AI'}
                  </button>
                )}
              </div>
            )}
          </>
        )}

        {/* APPROVAL detail */}
        {item.type === 'approval' && (
          <>
            <div className="bg-slate-50 border border-slate-200 rounded-xl p-4">
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Draft Content</p>
              {Object.entries((payload.content as Record<string, unknown>) ?? {}).map(([k, v]) => (
                <div key={k} className="flex gap-2 text-sm mb-1.5">
                  <span className="text-slate-400 capitalize min-w-[6rem]">{k.replace(/_/g, ' ')}</span>
                  <span className="text-slate-700 font-medium">{String(v)}</span>
                </div>
              ))}
            </div>
            {canApprove && (
              <div className="flex gap-3">
                <button
                  onClick={handleApprove}
                  disabled={approving}
                  className="flex-1 py-2.5 text-sm font-medium rounded-xl bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 flex items-center justify-center gap-2 transition-colors"
                >
                  <Check className="w-4 h-4" />
                  {approving ? 'Approving…' : 'Approve'}
                </button>
                <button
                  onClick={handleReject}
                  disabled={rejecting}
                  className="flex-1 py-2.5 text-sm font-medium rounded-xl bg-slate-100 text-slate-700 hover:bg-red-50 hover:text-red-700 disabled:opacity-50 flex items-center justify-center gap-2 transition-colors"
                >
                  <X className="w-4 h-4" />
                  {rejecting ? 'Rejecting…' : 'Reject'}
                </button>
              </div>
            )}
          </>
        )}

        {/* ALERT detail */}
        {item.type === 'alert' && (
          <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 space-y-2">
            <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide">Inventory Details</p>
            {[
              ['Product',   payload.product_name],
              ['Qty on Hand', payload.qty_on_hand],
              ['Weeks Left',  payload.weeks_remaining != null ? `${Number(payload.weeks_remaining).toFixed(1)}w` : '—'],
              ['Status',    String(payload.stock_status ?? '').replace('_', ' ')],
            ].map(([l, v]) => (
              <div key={String(l)} className="flex justify-between text-sm">
                <span className="text-slate-500">{l}</span>
                <span className="font-medium text-slate-800">{String(v ?? '—')}</span>
              </div>
            ))}
          </div>
        )}

        {/* HEALTH EVENT detail */}
        {item.type === 'health_event' && (
          <div className="bg-orange-50 border border-orange-200 rounded-xl p-4 space-y-2">
            <p className="text-xs font-semibold text-orange-700 uppercase tracking-wide">Account Health</p>
            {[
              ['Account',     payload.account_name],
              ['Status',      String(payload.health_status ?? '').replace('_', ' ')],
              ['Score',       payload.health_score != null ? `${payload.health_score}/100` : '—'],
              ['Last Order',  payload.last_order_date ? String(payload.last_order_date).slice(0, 10) : 'None'],
            ].map(([l, v]) => (
              <div key={String(l)} className="flex justify-between text-sm">
                <span className="text-slate-500">{l}</span>
                <span className="font-medium text-slate-800">{String(v ?? '—')}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Inline AI */}
      <div className="border-t border-slate-100 flex-shrink-0">
        <InlineAssistant
          context={{
            type:  item.type === 'email' ? 'inbox' : item.type === 'health_event' ? 'account' : 'inbox',
            label: item.title,
            data:  payload,
          }}
          initialSuggestion={
            item.type === 'health_event' && payload.health_status === 'dormant'
              ? `${item.title} has gone dormant. Want me to draft a re-engagement email?`
              : item.type === 'alert' && payload.stock_status === 'out_of_stock'
              ? `${item.title} is out of stock. Want me to draft a reorder PO?`
              : undefined
          }
        />
      </div>
    </div>
  );
}

// ── Main Inbox page ───────────────────────────────────────────────────────────
export default function Inbox() {
  const { items, unread_count, loading, selectedId, filter, fetch, setFilter, selectItem } =
    useInboxStore();

  const selected = items.find((i) => i.id === selectedId) ?? null;

  useEffect(() => {
    fetch();
    const t = setInterval(fetch, 30_000);
    return () => clearInterval(t);
  }, []);

  const filteredItems = filter === 'all' ? items : items.filter((i) => i.type === filter);

  return (
    <div className="flex h-full min-h-0 bg-white">
      {/* Left: Feed */}
      <div className="w-80 flex-shrink-0 flex flex-col border-r border-slate-200 min-h-0">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-4 border-b border-slate-100">
          <div className="flex items-center gap-2">
            <InboxIcon className="w-5 h-5 text-slate-700" />
            <h1 className="text-base font-semibold text-slate-900">Inbox</h1>
            {unread_count > 0 && (
              <span className="px-1.5 py-0.5 rounded-full text-xs font-bold bg-red-500 text-white">
                {unread_count > 99 ? '99+' : unread_count}
              </span>
            )}
          </div>
          <button
            onClick={fetch}
            disabled={loading}
            className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* Filter tabs */}
        <div className="flex gap-0.5 p-2 border-b border-slate-100 overflow-x-auto">
          {FILTERS.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`px-2.5 py-1 rounded-lg text-xs font-medium whitespace-nowrap transition-colors ${
                filter === key
                  ? 'bg-slate-900 text-white'
                  : 'text-slate-500 hover:bg-slate-100'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Feed */}
        <div className="flex-1 overflow-y-auto">
          {loading && filteredItems.length === 0 ? (
            <div className="space-y-0">
              {[...Array(6)].map((_, i) => (
                <div key={i} className="flex gap-3 px-4 py-3.5 border-b border-slate-100">
                  <div className="w-7 h-7 rounded-lg bg-slate-100 animate-pulse flex-shrink-0" />
                  <div className="flex-1 space-y-1.5">
                    <div className="h-3 bg-slate-100 rounded animate-pulse w-3/4" />
                    <div className="h-2.5 bg-slate-100 rounded animate-pulse w-full" />
                  </div>
                </div>
              ))}
            </div>
          ) : filteredItems.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-slate-400">
              <InboxIcon className="w-10 h-10 mb-3 text-slate-200" />
              <p className="text-sm font-medium">All caught up</p>
              <p className="text-xs mt-1">Nothing in your {filter !== 'all' ? filter : 'inbox'}</p>
            </div>
          ) : (
            filteredItems.map((item) => (
              <FeedRow
                key={item.id}
                item={item}
                selected={item.id === selectedId}
                onClick={() => selectItem(item.id === selectedId ? null : item.id)}
              />
            ))
          )}
        </div>
      </div>

      {/* Right: Detail panel */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {selected ? (
          <DetailPanel key={selected.id} item={selected} />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-slate-400 gap-3">
            <div className="w-14 h-14 rounded-2xl bg-slate-50 border border-slate-100 flex items-center justify-center">
              <InboxIcon className="w-6 h-6 text-slate-300" />
            </div>
            <div className="text-center">
              <p className="text-sm font-medium text-slate-500">Select an item</p>
              <p className="text-xs mt-1">Click anything in the feed to view details and take action</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
