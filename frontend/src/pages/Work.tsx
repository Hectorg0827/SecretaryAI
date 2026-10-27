import { useEffect, useState } from 'react';
import {
  CheckCircle2, Circle, Clock, Calendar,
  ShoppingCart, Mail, FileText, AlertTriangle,
  Check, X, RefreshCw, Briefcase
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../lib/api';
import { approvalNotice } from '../lib/approvalStatus';
import { useAuth } from '../hooks/useAuth';

// ── Types ─────────────────────────────────────────────────────────────────────
type WorkItemType = 'approval' | 'follow_up' | 'inventory';

interface WorkItem {
  id:          string;
  type:        WorkItemType;
  title:       string;
  subtitle:    string;
  due_date?:   string;
  priority:    'high' | 'medium' | 'low';
  done:        boolean;
  queued?:     boolean;
  payload?:    Record<string, unknown>;
}

const TYPE_CONFIG: Record<WorkItemType, { Icon: React.ElementType; color: string; bg: string }> = {
  approval:   { Icon: CheckCircle2,  color: 'text-emerald-600', bg: 'bg-emerald-50' },
  follow_up:  { Icon: Calendar,      color: 'text-blue-600',    bg: 'bg-blue-50'    },
  inventory:  { Icon: ShoppingCart,  color: 'text-amber-600',   bg: 'bg-amber-50'   },
};

const ACTION_ICON: Record<string, React.ElementType> = {
  draft_customer_email:   Mail,
  draft_purchase_order:   ShoppingCart,
  generate_external_report: FileText,
};

function isToday(date?: string): boolean {
  if (!date) return false;
  const d = new Date(date);
  const n = new Date();
  return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
}

function isThisWeek(date?: string): boolean {
  if (!date) return false;
  const d = new Date(date);
  const now = new Date();
  const weekEnd = new Date(now);
  weekEnd.setDate(now.getDate() + 7);
  return d > now && d <= weekEnd;
}

function isOverdue(date?: string): boolean {
  if (!date) return false;
  return new Date(date) < new Date();
}

function fmtDate(d?: string): string {
  if (!d) return '';
  return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// ── Work item card ────────────────────────────────────────────────────────────
function WorkCard({
  item,
  onApprove,
  onReject,
  onDone,
  allowDraftPO = false,
}: {
  item: WorkItem;
  onApprove?: (id: string) => void;
  onReject?:  (id: string) => void;
  onDone?:    (id: string) => void;
  allowDraftPO?: boolean;
}) {
  const cfg = TYPE_CONFIG[item.type];
  const { Icon } = cfg;
  const ActionIcon = item.payload?.action_type
    ? (ACTION_ICON[String(item.payload.action_type)] ?? FileText)
    : Icon;

  const overdue = isOverdue(item.due_date) && !item.done;
  const canComplete = item.type !== 'inventory' || allowDraftPO;

  return (
    <div
      className={`flex items-start gap-3 p-4 rounded-xl border transition-all
        ${item.done
          ? 'opacity-40 bg-slate-50 border-slate-100'
          : 'bg-white border-slate-200 hover:border-slate-300 shadow-sm'
        }`}
    >
      {/* Done toggle */}
      {item.type === 'approval' ? (
        <span className="mt-0.5 text-xs text-amber-700" aria-label="Requires an explicit approval decision">
          <Circle className="w-5 h-5" />
        </span>
      ) : (
        <button
          type="button"
          aria-label={item.type === 'inventory' ? `Create purchase order draft for ${item.title}` : `Complete ${item.title}`}
          onClick={() => !item.done && !item.queued && onDone?.(item.id)}
          disabled={item.done || item.queued || !canComplete}
          className={`mt-0.5 flex-shrink-0 w-5 h-5 rounded-full border-2 flex items-center justify-center transition-colors
            ${item.done ? 'bg-emerald-500 border-emerald-500' : 'border-slate-300 hover:border-emerald-400'} disabled:cursor-default`}
        >
          {item.done && <Check className="w-3 h-3 text-white" />}
        </button>
      )}

      {/* Icon */}
      <div className={`flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center ${cfg.bg}`}>
        <ActionIcon className={`w-4 h-4 ${cfg.color}`} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <p className={`text-sm font-medium ${item.done ? 'line-through text-slate-400' : 'text-slate-800'}`}>
          {item.title}
        </p>
        <p className="text-xs text-slate-500 mt-0.5 truncate">{item.subtitle}</p>
        {item.queued && <p className="mt-1 text-xs font-medium text-amber-800" role="status">PO draft queued for approval; inventory is not resolved.</p>}
        {item.due_date && (
          <p className={`flex items-center gap-1 text-xs mt-1.5 font-medium
            ${overdue ? 'text-red-600' : 'text-slate-400'}`}
          >
            <Clock className="w-3 h-3" />
            {overdue ? 'Overdue · ' : ''}{fmtDate(item.due_date)}
          </p>
        )}
      </div>

      {/* Action buttons (approvals only) */}
      {item.type === 'approval' && !item.done && (
        <div className="flex gap-1.5 flex-shrink-0">
          <button
            onClick={() => onApprove?.(item.id)}
            className="px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-600 text-white hover:bg-emerald-700 transition-colors flex items-center gap-1"
          >
            <Check className="w-3 h-3" /> Approve
          </button>
          <button
            onClick={() => onReject?.(item.id)}
            className="px-2.5 py-1.5 rounded-lg text-xs font-medium bg-slate-100 text-slate-600 hover:bg-red-50 hover:text-red-600 transition-colors"
          >
            <X className="w-3 h-3" />
          </button>
        </div>
      )}
    </div>
  );
}

// ── Section header ────────────────────────────────────────────────────────────
function Section({ label, count, children }: { label: string; count: number; children: React.ReactNode }) {
  if (count === 0) return null;
  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">{label}</h2>
        <span className="px-1.5 py-0.5 rounded text-xs font-bold bg-slate-100 text-slate-500">{count}</span>
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function Work() {
  const { canApprove, canAccessInventory, canDraftPO, role } = useAuth();
  const [items, setItems]   = useState<WorkItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [sourceErrors, setSourceErrors] = useState<string[]>([]);

  const load = async (silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);

    const next: WorkItem[] = [];
    const failures: string[] = [];

    // ── Pending approvals
    if (canApprove) {
      try {
        const res = await api.get<{ drafts: { id: string; action_type: string; content: Record<string, unknown>; created_at: string }[] }>(
          '/api/actions/pending'
        );
        for (const d of res.drafts ?? []) {
          const c = d.content ?? {};
          let title = d.action_type.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());
          let subtitle = '';
          if (d.action_type.includes('email')) {
            title    = `Email draft to ${c.to ?? 'recipient'}`;
            subtitle = String(c.subject ?? '');
          } else if (d.action_type.includes('purchase_order')) {
            title    = `PO draft — ${c.vendor_name ?? 'Vendor'}`;
            subtitle = c.total_amount ? `$${Number(c.total_amount).toLocaleString()}` : '';
          }
          next.push({
            id:       `approval:${d.id}`,
            type:     'approval',
            title,
            subtitle,
            priority: 'high',
            done:     false,
            payload:  { draft_id: d.id, action_type: d.action_type, content: c },
          });
        }
      } catch { failures.push('Approvals'); }
    }

    // ── Follow-up notes (pending, with due dates)
    if (role === 'owner' || role === 'manager') try {
      const res = await api.get<{ notes: { id: string; text: string; account_name?: string; due_date?: string; done: boolean }[] }>(
        '/api/dashboard/notes'
      );
      for (const n of (res.notes ?? []).filter((n) => !n.done)) {
        next.push({
          id:       `followup:${n.id}`,
          type:     'follow_up',
          title:    n.text,
          subtitle: n.account_name ? `Account: ${n.account_name}` : 'Follow-up',
          due_date: n.due_date,
          priority: isOverdue(n.due_date) ? 'high' : isToday(n.due_date) ? 'medium' : 'low',
          done:     false,
          payload:  { note_id: n.id },
        });
      }
    } catch { failures.push('Follow-up notes'); }

    // ── Critical inventory (auto-suggestions)
    if (canAccessInventory) try {
      const res = await api.get<{ alerts: { item_id: string; product_name: string; stock_status: string; needs_po: boolean }[] }>(
        '/api/inventory/alerts'
      );
      for (const item of (res.alerts ?? []).filter((i) => i.needs_po)) {
        next.push({
          id:       `inventory:${item.item_id}`,
          type:     'inventory',
          title:    `Draft PO — ${item.product_name}`,
          subtitle: `${item.stock_status.replace('_', ' ')} — reorder needed`,
          priority: item.stock_status === 'out_of_stock' ? 'high' : 'medium',
          done:     false,
          payload:  { item_id: item.item_id, product_name: item.product_name },
        });
      }
    } catch { failures.push('Inventory'); }

    // Sort: overdue → today → high priority
    next.sort((a, b) => {
      const aOver = isOverdue(a.due_date) ? 0 : isToday(a.due_date) ? 1 : 2;
      const bOver = isOverdue(b.due_date) ? 0 : isToday(b.due_date) ? 1 : 2;
      if (aOver !== bOver) return aOver - bOver;
      const pw = { high: 0, medium: 1, low: 2 };
      return pw[a.priority] - pw[b.priority];
    });

    setItems(next);
    setSourceErrors(failures);
    setLoading(false);
    setRefreshing(false);
  };

  useEffect(() => { load(); }, []);

  const handleApprove = async (itemId: string) => {
    const item = items.find((i) => i.id === itemId);
    const draftId = item?.payload?.draft_id;
    if (typeof draftId !== 'string') return;
    if (!window.confirm('Approve this draft? An external action may be queued and needs separate delivery confirmation.')) return;
    try {
      const result = await api.actions.approve(draftId);
      const notice = approvalNotice(result);
      setItems((prev) => prev.map((i) => i.id === itemId ? { ...i, done: true } : i));
      if (notice.severity === 'success') toast.success(notice.message);
      else toast(notice.message);
    } catch {
      toast.error('Approval failed');
    }
  };

  const handleReject = async (itemId: string) => {
    const item = items.find((i) => i.id === itemId);
    const draftId = item?.payload?.draft_id;
    if (typeof draftId !== 'string') return;
    try {
      await api.post(`/api/actions/reject/${draftId}`, { reason: 'Rejected from work queue' });
      setItems((prev) => prev.map((i) => i.id === itemId ? { ...i, done: true } : i));
      toast.success('Rejected');
    } catch {
      toast.error('Rejection failed');
    }
  };

  const handleDone = async (itemId: string) => {
    const item = items.find((i) => i.id === itemId);
    if (!item) return;

    if (item.type === 'follow_up' && item.payload?.note_id) {
      try {
        await api.patch(`/api/dashboard/notes/${item.payload.note_id}`, { done: true });
        setItems((prev) => prev.map((i) => i.id === itemId ? { ...i, done: true } : i));
      } catch { toast.error('Follow-up could not be completed; no change was recorded.'); }
      return;
    }
    if (item.type === 'inventory' && item.payload?.item_id) {
      try {
        const result = await api.post<{ status: string; draft_id: string }>('/api/actions/draft-po', {
          item_id:      item.payload.item_id,
          product_name: item.payload.product_name,
        });
        if (result.status !== 'queued' || !result.draft_id) throw new Error('Draft queue confirmation missing');
        setItems((prev) => prev.map((i) => i.id === itemId ? { ...i, queued: true } : i));
        toast.success('PO draft queued for approval; no order was sent.');
      } catch {
        toast.error('PO draft was not confirmed; please verify before retrying.');
      }
      return;
    }
  };

  const pending = items.filter((i) => !i.done);
  const done    = items.filter((i) =>  i.done);

  const overdue   = pending.filter((i) => isOverdue(i.due_date));
  const today     = pending.filter((i) => !isOverdue(i.due_date) && isToday(i.due_date));
  const thisWeek  = pending.filter((i) => !isOverdue(i.due_date) && !isToday(i.due_date) && isThisWeek(i.due_date));
  const later     = pending.filter((i) => !isOverdue(i.due_date) && !isToday(i.due_date) && !isThisWeek(i.due_date));

  const totalPending = pending.length;
  const totalDone    = done.length;
  const pct = totalPending + totalDone > 0
    ? Math.round((totalDone / (totalPending + totalDone)) * 100)
    : 0;

  return (
    <div className="flex flex-col h-full bg-slate-50">
      {/* Header */}
      <div className="bg-white border-b border-slate-200 px-6 py-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Briefcase className="w-5 h-5 text-slate-700" />
            <h1 className="text-lg font-semibold text-slate-900">Work Queue</h1>
            {totalPending > 0 && (
              <span className="px-2 py-0.5 rounded-full text-xs font-bold bg-slate-900 text-white">
                {totalPending}
              </span>
            )}
          </div>
          <button
            onClick={() => load(true)}
            disabled={refreshing}
            className="p-2 rounded-lg hover:bg-slate-100 text-slate-400 transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* Progress bar */}
        {totalPending + totalDone > 0 && (
          <div>
            <div className="flex justify-between text-xs text-slate-500 mb-1.5">
              <span>{totalDone} of {totalPending + totalDone} done today</span>
              <span>{pct}%</span>
            </div>
            <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-emerald-500 rounded-full transition-all duration-500"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6 max-w-2xl mx-auto w-full">
        {!loading && sourceErrors.length > 0 && (
          <div role="alert" className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
            <AlertTriangle className="w-4 h-4 inline mr-1" />
            Work data is incomplete. Unavailable sources: {sourceErrors.join(', ')}. Do not treat this as an all-clear.
          </div>
        )}
        {loading ? (
          <div className="space-y-3">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="h-16 bg-white rounded-xl border border-slate-200 animate-pulse" />
            ))}
          </div>
        ) : totalPending === 0 && totalDone === 0 && sourceErrors.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-slate-400 gap-3">
            <div className="w-14 h-14 rounded-2xl bg-slate-100 border border-slate-200 flex items-center justify-center">
              <CheckCircle2 className="w-6 h-6 text-slate-300" />
            </div>
            <div className="text-center">
              <p className="text-sm font-medium text-slate-500">All clear</p>
              <p className="text-xs mt-1">No pending work items</p>
            </div>
          </div>
        ) : (
          <>
            <Section label="⚠ Overdue" count={overdue.length}>
              {overdue.map((item) => (
                <WorkCard key={item.id} item={item} onApprove={handleApprove} onReject={handleReject} onDone={handleDone} allowDraftPO={canDraftPO} />
              ))}
            </Section>

            <Section label="Today" count={today.length}>
              {today.map((item) => (
                <WorkCard key={item.id} item={item} onApprove={handleApprove} onReject={handleReject} onDone={handleDone} allowDraftPO={canDraftPO} />
              ))}
            </Section>

            <Section label="This week" count={thisWeek.length}>
              {thisWeek.map((item) => (
                <WorkCard key={item.id} item={item} onApprove={handleApprove} onReject={handleReject} onDone={handleDone} allowDraftPO={canDraftPO} />
              ))}
            </Section>

            <Section label="Later" count={later.length}>
              {later.map((item) => (
                <WorkCard key={item.id} item={item} onApprove={handleApprove} onReject={handleReject} onDone={handleDone} allowDraftPO={canDraftPO} />
              ))}
            </Section>

            {done.length > 0 && (
              <Section label={`Completed (${done.length})`} count={done.length}>
                {done.slice(0, 5).map((item) => (
                  <WorkCard key={item.id} item={item} onDone={handleDone} />
                ))}
              </Section>
            )}
          </>
        )}
      </div>
    </div>
  );
}
