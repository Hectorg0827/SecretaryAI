import React, { useEffect, useState } from 'react';
import clsx from 'clsx';
import { CheckCircle, XCircle, Mail, ShoppingCart, FileText, Loader2, ClipboardList, AlertTriangle } from 'lucide-react';
import toast from 'react-hot-toast';
import { api, describeApproval, Draft, NeedsCheckingItem } from '../../lib/api';
import { Card, CardHeader } from '../ui/Card';
import { useAuth } from '../../hooks/useAuth';

const ACTION_META: Record<string, { label: string; icon: React.ElementType; color: string }> = {
  draft_customer_email:    { label: 'Email',          icon: Mail,          color: 'text-blue-600'   },
  draft_purchase_order:    { label: 'Purchase Order',  icon: ShoppingCart,  color: 'text-emerald-600'},
  generate_external_report:{ label: 'External Report', icon: FileText,      color: 'text-purple-600' },
};

function DraftPreview({ draft }: { draft: Draft }) {
  const c = draft.content;
  if (draft.action_type === 'draft_customer_email') {
    return (
      <div className="text-xs text-slate-600 bg-slate-50 rounded-lg p-2.5 space-y-0.5 mt-2">
        <div><span className="text-slate-400">To:</span> {String(c.to ?? '')}</div>
        <div><span className="text-slate-400">Subject:</span> {String(c.subject ?? '')}</div>
        <div className="line-clamp-2 text-slate-500 mt-1">{String(c.body ?? '')}</div>
      </div>
    );
  }
  if (draft.action_type === 'draft_purchase_order') {
    return (
      <div className="text-xs text-slate-600 bg-slate-50 rounded-lg p-2.5 space-y-0.5 mt-2">
        <div><span className="text-slate-400">Vendor:</span> {String(c.vendor_name ?? '')}</div>
        <div><span className="text-slate-400">Total:</span> <span className="font-medium">${Number(c.subtotal ?? 0).toLocaleString()}</span></div>
      </div>
    );
  }
  return null;
}

function DraftCard({ draft, onRemove, canApprove, onUncertain }: { draft: Draft; onRemove: (id: string) => void; canApprove: boolean; onUncertain?: () => void }) {
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null);
  const meta = ACTION_META[draft.action_type] ?? { label: draft.action_type, icon: FileText, color: 'text-slate-600' };
  const Icon = meta.icon;

  const approve = async () => {
    setBusy('approve');
    try {
      const outcome = describeApproval(await api.actions.approve(draft.id));
      if (outcome.ok) toast.success(outcome.message); else toast.error(outcome.message, { duration: 8000 });
      if (outcome.done) onRemove(draft.id);
      if (!outcome.ok) onUncertain?.();
    } catch (err) { toast.error(err instanceof Error ? err.message : 'Could not approve'); }
    finally   { setBusy(null); }
  };

  const reject = async () => {
    setBusy('reject');
    try {
      await api.actions.reject(draft.id);
      onRemove(draft.id);
    } catch { toast.error('Could not reject'); }
    finally   { setBusy(null); }
  };

  return (
    <div className="px-4 py-3 border-l-4 border-l-amber-400 bg-amber-50/40">
      <div className="flex items-center gap-2 mb-1">
        <Icon size={13} className={meta.color} />
        <span className="text-xs font-semibold text-slate-700">{meta.label}</span>
        <span className="ml-auto text-xs text-slate-400">
          {new Date(draft.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>
      <DraftPreview draft={draft} />
      {canApprove && (
        <div className="flex gap-2 mt-2.5">
          <button
            onClick={approve}
            disabled={!!busy}
            className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-white bg-emerald-600 hover:bg-emerald-700 rounded-lg transition-colors disabled:opacity-50"
          >
            {busy === 'approve' ? <Loader2 size={11} className="animate-spin" /> : <CheckCircle size={11} />}
            Approve
          </button>
          <button
            onClick={reject}
            disabled={!!busy}
            className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-slate-600 bg-white border border-slate-200 hover:bg-slate-50 rounded-lg transition-colors disabled:opacity-50"
          >
            {busy === 'reject' ? <Loader2 size={11} className="animate-spin" /> : <XCircle size={11} />}
            Reject
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * An approved action we could not confirm (for example the connection dropped
 * after the request was sent). It stays here until a person checks and says
 * what happened. Neither button sends anything.
 */
function NeedsCheckingCard({ item, onSettled }: { item: NeedsCheckingItem; onSettled: () => void }) {
  const [busy, setBusy] = useState(false);
  const settle = async (outcome: 'went_through' | 'did_not') => {
    setBusy(true);
    try {
      await api.actions.settle(item.id, outcome);
      toast.success(outcome === 'went_through' ? 'Marked as done.' : 'Put back in the approval queue. It has not been sent again.');
      onSettled();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not save that');
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="px-5 py-3.5 bg-amber-50/60">
      <div className="flex items-start gap-2">
        <AlertTriangle size={14} className="text-amber-600 mt-0.5 flex-shrink-0" />
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-800">{item.summary}</div>
          <div className="text-xs text-slate-600 mt-1">{item.how_to_check}</div>
          <div className="text-[11px] text-slate-400 mt-1">
            Reference {item.id.slice(0, 8)}
            {item.attempted_at ? ` · tried ${new Date(item.attempted_at).toLocaleString()}` : ''}
          </div>
          <div className="flex flex-wrap gap-2 mt-2">
            <button type="button" disabled={busy} onClick={() => settle('went_through')}
              className="text-xs font-medium text-emerald-700 border border-emerald-200 bg-white rounded-lg px-2.5 py-1.5 hover:bg-emerald-50 disabled:opacity-50">
              I checked — it went through
            </button>
            <button type="button" disabled={busy} onClick={() => settle('did_not')}
              className="text-xs font-medium text-slate-700 border border-slate-200 bg-white rounded-lg px-2.5 py-1.5 hover:bg-slate-50 disabled:opacity-50">
              I checked — it did not
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function PendingApprovals() {
  const [drafts,  setDrafts]  = useState<Draft[]>([]);
  const [checking, setChecking] = useState<NeedsCheckingItem[]>([]);
  const [loading, setLoading] = useState(true);
  const { canApprove } = useAuth();

  const fetch = () => {
    api.actions.pending()
      .then((r) => setDrafts(r.drafts ?? []))
      .catch(() => setDrafts([]))
      .finally(() => setLoading(false));
    if (canApprove) {
      api.actions.needsChecking().then((r) => setChecking(r.items ?? [])).catch(() => setChecking([]));
    }
  };

  useEffect(() => {
    fetch();
    const t = setInterval(fetch, 30_000);
    return () => clearInterval(t);
  }, []);

  const remove = (id: string) => setDrafts((p) => p.filter((d) => d.id !== id));

  return (
    <Card>
      <CardHeader title="Pending Approvals" count={drafts.length || undefined} />
      {checking.length > 0 && (
        <div className="border-b border-amber-200">
          <div className="px-5 pt-3 text-xs font-semibold text-amber-800">
            Needs checking ({checking.length}) — we could not confirm these went through
          </div>
          <div className="divide-y divide-amber-100">
            {checking.map((c) => <NeedsCheckingCard key={c.id} item={c} onSettled={fetch} />)}
          </div>
        </div>
      )}
      {loading ? (
        <div className="flex items-center gap-2 px-5 py-4 text-xs text-slate-400">
          <Loader2 size={13} className="animate-spin" /> Loading…
        </div>
      ) : drafts.length === 0 ? (
        <div className="flex items-center gap-2 px-5 py-4 text-sm text-slate-400">
          <ClipboardList size={15} className="opacity-50" />
          Nothing waiting for approval
        </div>
      ) : (
        <div className="divide-y divide-slate-100">
          {drafts.map((d) => <DraftCard key={d.id} draft={d} onRemove={remove} canApprove={canApprove} onUncertain={fetch} />)}
        </div>
      )}
    </Card>
  );
}
