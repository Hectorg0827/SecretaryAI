import React from 'react';
import { api } from '../../lib/api';
import { BUTTON_PRIMARY, ConfirmPanel, ErrorNote, useConfirmedWrite } from './logisticsShared';

export type ApprovedPurchaseOrder = { po_id: string; status: string; inventory_posted: boolean };

/**
 * Independent approval of a draft PO (POST /orders/{po}/approve). Only rendered
 * for owner/manager; the server also rejects the person who created the draft.
 */
export function ApprovePurchaseOrder({ poNumber, createdBy, currentUser, onApproved }: {
  poNumber: string;
  createdBy: string | null | undefined;
  currentUser: string | null;
  onApproved: (result: ApprovedPurchaseOrder) => void;
}) {
  const write = useConfirmedWrite<ApprovedPurchaseOrder>();

  if (currentUser && createdBy && currentUser === createdBy) {
    return <p className="text-sm text-slate-700 bg-slate-50 rounded-lg p-3">
      You created this draft, so a different owner or manager must approve it.
    </p>;
  }

  async function confirm() {
    const result = await write.send(() => api.post<ApprovedPurchaseOrder>(
      `/api/logistics/orders/${encodeURIComponent(poNumber)}/approve`, {}));
    if (result) onApproved(result);
  }

  return <div className="space-y-2">
    {!write.confirming && <button type="button" className={BUTTON_PRIMARY} onClick={write.open}>
      Approve purchase order
    </button>}
    {write.confirming && <ConfirmPanel title="Approve this purchase order?" confirmLabel="Approve"
      busy={write.busy} onConfirm={confirm} onCancel={write.cancel}>
      <p>
        Approving <strong>{poNumber}</strong> marks it as approved in SecretaryAI so the warehouse can record
        deliveries against it.
      </p>
      <p>
        Nothing is sent to the supplier, stock does not change and nothing is written to QuickBooks.
        The approval is recorded under your name and cannot be undone from this screen.
      </p>
    </ConfirmPanel>}
    {Boolean(write.error) && <ErrorNote error={write.error} fallback="The purchase order was not approved." />}
  </div>;
}
