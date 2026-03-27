/**
 * Approval Queue — shows pending DRAFT_AND_WAIT actions that need human review.
 * The AI never executes these automatically; the user must approve, edit, or reject.
 */
import React, { useEffect, useState } from 'react';
import clsx from 'clsx';
import { CheckCircle, XCircle, Edit2, Mail, ShoppingCart, FileText, Loader2 } from 'lucide-react';
import { api } from '../../lib/api';

interface Draft {
  id: string;
  action_type: string;
  content: Record<string, unknown>;
  created_at: string;
  status: 'pending' | 'approved' | 'rejected' | 'edited_and_approved';
}

const ACTION_META: Record<string, { label: string; icon: React.ElementType; color: string }> = {
  draft_customer_email: { label: 'Customer Email', icon: Mail, color: 'text-blue-600' },
  draft_purchase_order: { label: 'Purchase Order', icon: ShoppingCart, color: 'text-green-600' },
  generate_external_report: { label: 'External Report', icon: FileText, color: 'text-purple-600' },
};

export function ApprovalQueue() {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [loading, setLoading] = useState(true);
  const [processing, setProcessing] = useState<string | null>(null);

  const fetchDrafts = async () => {
    try {
      const data = await api.get<{ drafts: Draft[] }>('/api/actions/pending');
      setDrafts(data.drafts);
    } catch (e) {
      console.error('Failed to load drafts:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDrafts();
    const interval = setInterval(fetchDrafts, 30_000); // poll every 30s
    return () => clearInterval(interval);
  }, []);

  const approve = async (draftId: string) => {
    setProcessing(draftId);
    try {
      await api.post(`/api/actions/approve/${draftId}`, {});
      setDrafts((prev) => prev.filter((d) => d.id !== draftId));
    } finally {
      setProcessing(null);
    }
  };

  const reject = async (draftId: string) => {
    setProcessing(draftId);
    try {
      await api.post(`/api/actions/reject/${draftId}`, { reason: 'Rejected by user' });
      setDrafts((prev) => prev.filter((d) => d.id !== draftId));
    } finally {
      setProcessing(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-gray-400 text-sm p-4">
        <Loader2 size={14} className="animate-spin" /> Loading pending actions...
      </div>
    );
  }

  if (drafts.length === 0) {
    return (
      <div className="text-sm text-gray-400 p-4">
        No pending actions — SecretaryAI has nothing waiting for your approval.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">
        Pending Approval ({drafts.length})
      </h2>

      {drafts.map((draft) => {
        const meta = ACTION_META[draft.action_type] ?? {
          label: draft.action_type,
          icon: FileText,
          color: 'text-gray-600',
        };
        const Icon = meta.icon;
        const isProcessing = processing === draft.id;

        return (
          <div
            key={draft.id}
            className="bg-white rounded-xl border border-amber-200 shadow-sm p-4 space-y-3"
          >
            {/* Header */}
            <div className="flex items-center gap-2">
              <Icon size={16} className={meta.color} />
              <span className="font-medium text-sm text-gray-800">{meta.label}</span>
              <span className="ml-auto text-xs text-gray-400">
                {new Date(draft.created_at).toLocaleTimeString()}
              </span>
            </div>

            {/* Content preview */}
            <DraftPreview actionType={draft.action_type} content={draft.content} />

            {/* Actions */}
            <div className="flex items-center gap-2 pt-1">
              <button
                onClick={() => approve(draft.id)}
                disabled={isProcessing}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-white bg-green-600 hover:bg-green-700 rounded-lg transition-colors disabled:opacity-50"
              >
                <CheckCircle size={14} />
                Approve &amp; Send
              </button>

              <button
                onClick={() => reject(draft.id)}
                disabled={isProcessing}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors disabled:opacity-50"
              >
                <XCircle size={14} />
                Reject
              </button>

              {isProcessing && <Loader2 size={14} className="animate-spin text-gray-400 ml-auto" />}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DraftPreview({ actionType, content }: { actionType: string; content: Record<string, unknown> }) {
  if (actionType === 'draft_customer_email') {
    return (
      <div className="text-sm space-y-1 bg-gray-50 rounded-lg p-3">
        <div><span className="text-gray-500">To:</span> <span className="text-gray-800">{String(content.to ?? '')}</span></div>
        <div><span className="text-gray-500">Subject:</span> <span className="text-gray-800">{String(content.subject ?? '')}</span></div>
        <div className="text-gray-600 line-clamp-3 mt-1">{String(content.body ?? '')}</div>
      </div>
    );
  }

  if (actionType === 'draft_purchase_order') {
    const items = (content.line_items as any[]) ?? [];
    return (
      <div className="text-sm bg-gray-50 rounded-lg p-3 space-y-1">
        <div><span className="text-gray-500">Vendor:</span> <span className="text-gray-800">{String(content.vendor_name ?? '')}</span></div>
        <div><span className="text-gray-500">Items:</span> <span className="text-gray-800">{items.length} line item(s)</span></div>
        <div><span className="text-gray-500">Total:</span> <span className="font-medium text-gray-800">${Number(content.subtotal ?? 0).toLocaleString()}</span></div>
        {Boolean(content.created_reason) && (
          <div className="text-gray-500 text-xs mt-1">{String(content.created_reason)}</div>
        )}
      </div>
    );
  }

  // Generic fallback
  return (
    <pre className="text-xs text-gray-600 bg-gray-50 rounded p-2 overflow-x-auto">
      {JSON.stringify(content, null, 2)}
    </pre>
  );
}
