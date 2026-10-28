import React, { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { useAuth } from '../../hooks/useAuth';
import { ApprovePurchaseOrder } from './ApprovePurchaseOrder';
import { CreatePurchaseOrderForm } from './CreatePurchaseOrderForm';
import { PipelineStage } from './PipelineStage';
import { PurchaseEvidence } from './PurchaseEvidence';
import { RecordReceiptForm } from './RecordReceiptForm';
import {
  APPROVER_ROLES, BUTTON_SECONDARY, PURCHASING_ROLES, ServerNote, currentCompanyId, currentUserId,
  formatDateTime, roleAllowed,
} from './logisticsShared';

type Order = {
  id: string; po_number: string; supplier_ref: string; status: string; created_at: string;
  approved_at?: string | null; created_by?: string | null; approved_by?: string | null;
};
type Receipt = { id: string; receipt_key: string; status?: string; created_at?: string; posted_at?: string };
type Detail = {
  order: Order;
  lines: { sku: string; qty_ordered: number | string; qty_received: number | string }[];
  staged_receipts: Receipt[];
  posted_receipts: Receipt[];
  ordered_cases: string;
  received_cases: string;
  remaining_cases: string;
  accounting_reconciled: boolean;
};

const STATUS_LABELS: Record<string, string> = {
  draft: 'Draft — waiting for approval',
  approved: 'Approved — ready to receive',
  partially_received: 'Partly received',
  received: 'Fully received',
};
const RECEIPT_STATUS_LABELS: Record<string, string> = {
  pending: 'waiting for approval',
  posted: 'approved and added to stock',
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/** Read authoritative purchasing records. Empty results and failed reads are distinct. */
export function PurchasingRecords() {
  const { role } = useAuth();
  const userId = currentUserId();
  const companyId = currentCompanyId();
  const [orders, setOrders] = useState<Order[]>([]);
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [revision, setRevision] = useState(0);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError('');
    api.get<{ orders: Order[] }>('/api/logistics/orders').then((data) => {
      if (!Array.isArray(data?.orders)) throw new Error('Purchase order response is unavailable.');
      if (current) setOrders(data.orders);
    }).catch((reason: unknown) => {
      if (current) setError(reason instanceof Error ? reason.message : 'Unable to load purchase orders.');
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [revision]);

  useEffect(() => {
    let current = true;
    setDetail(null);
    setDetailError('');
    if (!selected) return () => { current = false; };
    setDetailLoading(true);
    api.get<Detail>(`/api/logistics/orders/${encodeURIComponent(selected)}`).then((data) => {
      if (!data?.order || !Array.isArray(data.lines) || !Array.isArray(data.staged_receipts) || !Array.isArray(data.posted_receipts)) {
        throw new Error('Purchase order details are unavailable.');
      }
      if (current) setDetail(data);
    }).catch((reason: unknown) => {
      if (current) setDetailError(reason instanceof Error ? reason.message : 'Unable to load purchase order details.');
    }).finally(() => { if (current) setDetailLoading(false); });
    return () => { current = false; };
  }, [selected, revision]);

  function reloadWith(message: string, poNumber?: string) {
    setNotice(message);
    if (poNumber) setSelected(poNumber);
    setRevision((value) => value + 1);
  }

  const order = detail?.order;
  const canReceive = order && roleAllowed(role, PURCHASING_ROLES)
    && (order.status === 'approved' || order.status === 'partially_received');

  return <section className="p-4 sm:p-6 space-y-4" aria-label="Saved purchase orders">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 className="font-semibold text-slate-900">Saved purchase orders</h2>
        <p className="text-sm text-slate-600">Purchase approvals and receiving records from your company ledger.</p>
      </div>
      <div className="flex gap-2">
        <button type="button" disabled={loading} onClick={() => { setNotice(''); setRevision((value) => value + 1); }}
          className={BUTTON_SECONDARY}>Refresh</button>
        {roleAllowed(role, PURCHASING_ROLES) && !creating && <button type="button" className={BUTTON_SECONDARY}
          onClick={() => { setNotice(''); setCreating(true); }}>New purchase order</button>}
      </div>
    </div>
    {creating && <CreatePurchaseOrderForm onCancel={() => setCreating(false)} onCreated={(created) => {
      setCreating(false);
      reloadWith(`Saved by the server: purchase order ${created.po_number} is now "${statusLabel(created.status)}" with ${created.line_count} product line${created.line_count === 1 ? '' : 's'}. It still needs approval from a different owner or manager.`, created.po_number);
    }} />}
    {notice && <ServerNote>{notice}</ServerNote>}
    {loading ? <p role="status">Loading purchase orders…</p> : error ?
      <p role="alert" className="text-red-700">{error}</p> : orders.length === 0 ?
      <p className="text-slate-600">No saved purchase orders. Warehouse and product setup must be completed before the purchasing pilot.</p> :
      <>
        <label className="block text-sm font-medium text-slate-700" htmlFor="saved-po">Purchase order</label>
        <select id="saved-po" className="w-full border border-slate-300 rounded-lg p-2 text-slate-900 bg-white"
          value={selected} onChange={(event) => { setNotice(''); setSelected(event.target.value); }}>
          <option value="">Choose a purchase order</option>
          {orders.map((item) => <option key={item.id} value={item.po_number}>
            {item.po_number} · {item.supplier_ref} · {statusLabel(item.status)}
          </option>)}
        </select>
        <p className="text-xs text-slate-600">Showing the latest {orders.length} orders (up to 100).</p>
      </>}
    {selected && detailLoading && <p role="status">Loading receiving records…</p>}
    {selected && detailError && <p role="alert" className="text-red-700">{detailError}</p>}
    {detail && order && !loading && !error && <article className="space-y-4 border border-slate-200 rounded-xl p-4">
      <div>
        <h3 className="font-semibold text-slate-900">{order.po_number} · {statusLabel(order.status)}</h3>
        <p className="text-sm text-slate-600">
          Supplier {order.supplier_ref} · created {formatDateTime(order.created_at)}
          {order.approved_at ? ` · approved ${formatDateTime(order.approved_at)}` : ''}
        </p>
      </div>
      <dl className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm">
        <div><dt className="text-slate-600">Ordered cases</dt><dd className="font-semibold">{detail.ordered_cases}</dd></div>
        <div><dt className="text-slate-600">Received cases, all conditions</dt><dd className="font-semibold">{detail.received_cases}</dd></div>
        <div><dt className="text-slate-600">Remaining cases</dt><dd className="font-semibold">{detail.remaining_cases}</dd></div>
      </dl>
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-left">
          <thead><tr><th className="p-2">SKU</th><th className="p-2">Ordered</th><th className="p-2">Received</th></tr></thead>
          <tbody>{detail.lines.map((line) => <tr key={line.sku} className="border-t border-slate-200">
            <td className="p-2">{line.sku}</td><td className="p-2">{line.qty_ordered}</td><td className="p-2">{line.qty_received}</td>
          </tr>)}</tbody>
        </table>
      </div>
      {order.status === 'draft' && roleAllowed(role, APPROVER_ROLES) &&
        <ApprovePurchaseOrder poNumber={order.po_number} createdBy={order.created_by} currentUser={userId}
          onApproved={(result) => reloadWith(`Approved by the server: purchase order ${order.po_number} is now "${statusLabel(result.status)}". Stock has not changed.`)} />}
      {order.status === 'draft' && !roleAllowed(role, APPROVER_ROLES) &&
        <p className="text-sm text-slate-600">This draft must be approved by an owner or manager before deliveries can be recorded.</p>}
      <h4 className="font-medium">Receipt submissions</h4>
      {detail.staged_receipts.length ? <ul className="list-disc pl-5 text-sm">{detail.staged_receipts.map((receipt) =>
        <li key={receipt.id}>{receipt.receipt_key} · {RECEIPT_STATUS_LABELS[receipt.status ?? ''] ?? receipt.status}</li>)}</ul> :
        <p className="text-sm text-slate-600">No receipt submissions.</p>}
      <h4 className="font-medium">Posted inventory receipts</h4>
      {detail.posted_receipts.length ? <ul className="list-disc pl-5 text-sm">{detail.posted_receipts.map((receipt) =>
        <li key={receipt.id}>{receipt.receipt_key}</li>)}</ul> : <p className="text-sm text-slate-600">No receipts posted to inventory.</p>}
      <p className="text-sm text-amber-800 bg-amber-50 rounded-lg p-3">QuickBooks reconciliation is not verified. Received totals include all conditions and must not be treated as sellable stock.</p>
      {canReceive && <RecordReceiptForm key={`${order.po_number}-${revision}`} poNumber={order.po_number} lines={detail.lines}
        onRecorded={(result) => reloadWith(result.replayed
          ? `This delivery was already submitted earlier with the same details; no duplicate was created. Server status: ${RECEIPT_STATUS_LABELS[result.status] ?? result.status}.`
          : `Submitted. Server status: ${RECEIPT_STATUS_LABELS[result.status] ?? result.status}. Stock ${result.inventory_posted ? 'was updated' : 'has not changed yet'}.`)} />}
      <PipelineStage poNumber={order.po_number} />
      <PurchaseEvidence poNumber={order.po_number} poStatus={order.status} role={role}
        currentUser={userId} companyId={companyId} />
    </article>}
  </section>;
}
