import React, { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { useAuth } from '../../hooks/useAuth';
import {
  APPROVER_ROLES, BUTTON_PRIMARY, BUTTON_SECONDARY, CARD, ConfirmPanel, ErrorNote, ServerNote, WarningNote,
  formatDateTime, roleAllowed, useConfirmedWrite,
} from './logisticsShared';

type ReceiptLine = {
  sku: string; lot_code?: string; accepted_qty?: number; damaged_qty?: number; quarantined_qty?: number;
};
type PendingReceipt = {
  id: string;
  po_id: string;
  receipt_key: string;
  payload: ReceiptLine[];
  submitted_by: string;
  created_at: string;
  can_approve: boolean;
};
type ApprovedReceipt = {
  receipt_id: string;
  staged_receipt_id: string;
  po_status?: string;
  status: string;
  replayed: boolean;
  inventory_posted: boolean;
  accounting_reconciled: boolean;
};

function totals(lines: ReceiptLine[]) {
  return lines.reduce((sum, line) => ({
    good: sum.good + (line.accepted_qty ?? 0),
    damaged: sum.damaged + (line.damaged_qty ?? 0),
    held: sum.held + (line.quarantined_qty ?? 0),
  }), { good: 0, damaged: 0, held: 0 });
}

function PendingReceiptCard({ receipt, poNumber, onApproved }: {
  receipt: PendingReceipt; poNumber: string; onApproved: (result: ApprovedReceipt) => void;
}) {
  const write = useConfirmedWrite<ApprovedReceipt>();
  const sum = totals(receipt.payload);

  async function confirm() {
    const result = await write.send(() => api.post<ApprovedReceipt>(
      `/api/logistics/receipts/${encodeURIComponent(receipt.id)}/approve`, {}));
    if (result) onApproved(result);
  }

  return <li className={CARD}>
    <div>
      <p className="font-medium text-slate-900">Purchase order {poNumber} · receipt {receipt.receipt_key}</p>
      <p className="text-sm text-slate-600">Submitted {formatDateTime(receipt.created_at)}</p>
    </div>
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left">
        <thead><tr className="text-slate-600"><th className="p-2">SKU</th><th className="p-2">Lot</th>
          <th className="p-2">Good</th><th className="p-2">Damaged</th><th className="p-2">Hold for inspection</th></tr></thead>
        <tbody>{receipt.payload.map((line, index) => <tr key={`${line.sku}-${index}`} className="border-t border-slate-200">
          <td className="p-2">{line.sku}</td><td className="p-2">{line.lot_code || '—'}</td>
          <td className="p-2">{line.accepted_qty ?? 0}</td><td className="p-2">{line.damaged_qty ?? 0}</td>
          <td className="p-2">{line.quarantined_qty ?? 0}</td>
        </tr>)}</tbody>
      </table>
    </div>
    {!receipt.can_approve && <p className="text-sm text-slate-700">
      You submitted this delivery, so a different owner or manager must approve it.
    </p>}
    {receipt.can_approve && !write.confirming && <button type="button" className={BUTTON_PRIMARY} onClick={write.open}>
      Approve and add to stock
    </button>}
    {write.confirming && <ConfirmPanel title="Approve this delivery and add it to stock?" confirmLabel="Approve and add to stock"
      busy={write.busy} onConfirm={confirm} onCancel={write.cancel}>
      <p>
        Approving receipt <strong>{receipt.receipt_key}</strong> for purchase order <strong>{poNumber}</strong> immediately
        adds it to your SecretaryAI stock: {sum.good} cases to available stock, {sum.damaged} to damaged stock and
        {' '}{sum.held} to the inspection hold. The purchase order’s received quantities are updated too.
      </p>
      <p>
        This cannot be undone from this screen. Nothing is written to QuickBooks and nothing is sent to the supplier.
      </p>
    </ConfirmPanel>}
    {Boolean(write.error) && <ErrorNote error={write.error} fallback="The delivery was not approved." />}
  </li>;
}

/** Deliveries waiting for an independent owner/manager approval (GET /receipts/pending). */
export function PendingReceipts() {
  const { role } = useAuth();
  const allowed = roleAllowed(role, APPROVER_ROLES);
  const [receipts, setReceipts] = useState<PendingReceipt[] | null>(null);
  const [orderNumbers, setOrderNumbers] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  const [revision, setRevision] = useState(0);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (!allowed) return undefined;
    let current = true;
    setReceipts(null);
    setError(null);
    Promise.all([
      api.get<{ pending_receipts: PendingReceipt[] }>('/api/logistics/receipts/pending'),
      api.get<{ orders: { id: string; po_number: string }[] }>('/api/logistics/orders'),
    ]).then(([pending, orders]) => {
      if (!Array.isArray(pending?.pending_receipts) || !Array.isArray(orders?.orders)) {
        throw new Error('Pending deliveries are unavailable.');
      }
      if (!current) return;
      setOrderNumbers(Object.fromEntries(orders.orders.map((order) => [order.id, order.po_number])));
      setReceipts(pending.pending_receipts.map((receipt) => ({
        ...receipt, payload: Array.isArray(receipt.payload) ? receipt.payload : [],
      })));
    }).catch((reason: unknown) => { if (current) setError(reason); });
    return () => { current = false; };
  }, [allowed, revision]);

  if (!allowed) {
    return <section aria-label="Deliveries waiting for approval" className="space-y-2">
      <h3 className="font-semibold text-slate-900">Deliveries waiting for approval</h3>
      <p className="text-sm text-slate-600">Deliveries are approved by an owner or manager.</p>
    </section>;
  }

  return <section aria-label="Deliveries waiting for approval" className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h3 className="font-semibold text-slate-900">Deliveries waiting for approval</h3>
        <p className="text-sm text-slate-600">Approving a delivery adds its cases to your SecretaryAI stock ledger.</p>
      </div>
      <button type="button" className={BUTTON_SECONDARY} disabled={receipts === null && !error}
        onClick={() => { setNotice(null); setRevision((value) => value + 1); }}>Refresh</button>
    </div>
    {notice && (notice.ok ? <ServerNote>{notice.text}</ServerNote> : <WarningNote>{notice.text}</WarningNote>)}
    {error ? <ErrorNote error={error} fallback="Unable to load deliveries waiting for approval." /> :
      receipts === null ? <p role="status" className="text-sm">Loading deliveries…</p> :
      receipts.length === 0 ? <p className="text-sm text-slate-600">No deliveries are waiting for approval.</p> :
      <ul className="space-y-3">{receipts.map((receipt) => {
        const poNumber = orderNumbers[receipt.po_id];
        if (!poNumber) {
          return <li key={receipt.id}><WarningNote>
            Receipt {receipt.receipt_key} belongs to a purchase order that is not in the latest 100 orders, so it cannot be approved here.
          </WarningNote></li>;
        }
        return <PendingReceiptCard key={receipt.id} receipt={receipt} poNumber={poNumber} onApproved={(result) => {
          setNotice(result.inventory_posted === true
            ? { ok: true, text: `Approved by the server: receipt ${receipt.receipt_key} was added to stock${result.replayed ? ' (it had already been posted earlier)' : ''}.`
              + (result.po_status ? ` Purchase order status: ${result.po_status.replace(/_/g, ' ')}.` : '')
              + ' QuickBooks has not been updated.' }
            : { ok: false, text: `The server did not confirm that receipt ${receipt.receipt_key} was added to stock. Refresh to check its status.` });
          setRevision((value) => value + 1);
        }} />;
      })}</ul>}
  </section>;
}
