import React, { useEffect, useState } from 'react';
import clsx from 'clsx';
import { CheckCircle, XCircle, Mail, ShoppingCart, FileText, Loader2, ClipboardList } from 'lucide-react';
import toast from 'react-hot-toast';
import { api, Draft } from '../../lib/api';
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

function DraftCard({ draft, onRemove, canApprove }: { draft: Draft; onRemove: (id: string) => void; canApprove: boolean }) {
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null);
  const meta = ACTION_META[draft.action_type] ?? { label: draft.action_type, icon: FileText, color: 'text-slate-600' };
  const Icon = meta.icon;

  const approve = async () => {
    setBusy('approve');
    try {
      await api.actions.approve(draft.id);
      toast.success('Approved & sent');
      onRemove(draft.id);
    } catch { toast.error('Could not approve'); }
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

export function PendingApprovals() {
  const [drafts,  setDrafts]  = useState<Draft[]>([]);
  const [loading, setLoading] = useState(true);
  const { canApprove } = useAuth();

  const fetch = () => {
    api.actions.pending()
      .then((r) => setDrafts(r.drafts ?? []))
      .catch(() => setDrafts([]))
      .finally(() => setLoading(false));
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
          {drafts.map((d) => <DraftCard key={d.id} draft={d} onRemove={remove} canApprove={canApprove} />)}
        </div>
      )}
    </Card>
  );
}
