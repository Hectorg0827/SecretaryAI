import React, { useEffect, useState } from 'react';
import { api } from '../../lib/api';

type Order = { id: string; po_number: string; supplier_ref: string; status: string; created_at: string };
type Receipt = { id: string; receipt_key: string; status?: string };
type Detail = {
  order: Order;
  lines: { sku: string; qty_ordered: number; qty_received: number }[];
  staged_receipts: Receipt[];
  posted_receipts: Receipt[];
  ordered_cases: string;
  received_cases: string;
  remaining_cases: string;
  accounting_reconciled: boolean;
};

/** Read authoritative purchasing records. Empty results and failed reads are distinct. */
export function PurchasingRecords() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError('');
    api.get<{ orders: Order[] }>('/api/logistics/orders').then((data) => {
      if (!Array.isArray(data.orders)) throw new Error('Purchase order response is unavailable.');
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
      if (!data.order || !Array.isArray(data.lines) || !Array.isArray(data.staged_receipts) || !Array.isArray(data.posted_receipts)) {
        throw new Error('Purchase order details are unavailable.');
      }
      if (current) setDetail(data);
    }).catch((reason: unknown) => {
      if (current) setDetailError(reason instanceof Error ? reason.message : 'Unable to load purchase order details.');
    }).finally(() => { if (current) setDetailLoading(false); });
    return () => { current = false; };
  }, [selected, revision]);

  return <section className="p-4 sm:p-6 space-y-4" aria-label="Saved purchase orders">
    <div className="flex items-center justify-between gap-3">
      <div>
        <h2 className="font-semibold text-slate-900">Saved purchase orders</h2>
        <p className="text-sm text-slate-600">Purchase approvals and receiving records from your company ledger.</p>
      </div>
      <button type="button" disabled={loading} onClick={() => setRevision((value) => value + 1)}
        className="border border-slate-300 rounded-lg px-3 py-2 text-sm text-slate-800 disabled:opacity-50">Refresh</button>
    </div>
    {loading ? <p role="status">Loading purchase orders…</p> : error ?
      <p role="alert" className="text-red-700">{error}</p> : orders.length === 0 ?
      <p className="text-slate-600">No saved purchase orders. Warehouse and product setup must be completed before the purchasing pilot.</p> :
      <>
        <label className="block text-sm font-medium text-slate-700" htmlFor="saved-po">Purchase order</label>
        <select id="saved-po" className="w-full border border-slate-300 rounded-lg p-2 text-slate-900 bg-white"
          value={selected} onChange={(event) => setSelected(event.target.value)}>
          <option value="">Choose a purchase order</option>
          {orders.map((order) => <option key={order.id} value={order.po_number}>
            {order.po_number} · {order.supplier_ref} · {order.status}
          </option>)}
        </select>
        <p className="text-xs text-slate-600">Showing the latest {orders.length} orders (up to 100).</p>
      </>}
    {selected && detailLoading && <p role="status">Loading receiving records…</p>}
    {selected && detailError && <p role="alert" className="text-red-700">{detailError}</p>}
    {detail && !loading && !error && <article className="space-y-4 border border-slate-200 rounded-xl p-4">
      <h3 className="font-semibold text-slate-900">{detail.order.po_number} · {detail.order.status}</h3>
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
      <h4 className="font-medium">Receipt submissions</h4>
      {detail.staged_receipts.length ? <ul className="list-disc pl-5 text-sm">{detail.staged_receipts.map((receipt) =>
        <li key={receipt.id}>{receipt.receipt_key} · {receipt.status}</li>)}</ul> : <p className="text-sm text-slate-600">No receipt submissions.</p>}
      <h4 className="font-medium">Posted inventory receipts</h4>
      {detail.posted_receipts.length ? <ul className="list-disc pl-5 text-sm">{detail.posted_receipts.map((receipt) =>
        <li key={receipt.id}>{receipt.receipt_key}</li>)}</ul> : <p className="text-sm text-slate-600">No receipts posted to inventory.</p>}
      <p className="text-sm text-amber-800 bg-amber-50 rounded-lg p-3">QuickBooks reconciliation is not verified. Received totals include all conditions and must not be treated as sellable stock.</p>
    </article>}
  </section>;
}
