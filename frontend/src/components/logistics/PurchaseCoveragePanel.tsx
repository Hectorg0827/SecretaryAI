import React, { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { useAuth } from '../../hooks/useAuth';
import { PURCHASING_ROLES, roleAllowed } from './logisticsShared';

type CoverageOrder = {
  po_number: string;
  po_status: string;
  supplier_ref?: string;
  shipment_link_status: string;
  linked_shipment_ids: string[];
  reported_unverified_shipment_ids: string[];
  requires_review: boolean;
  shipping_status_verified: boolean;
  container_receipt_verified: boolean;
  warehouse_receipt_attributed_to_shipment: boolean;
};

type Coverage = {
  purchase_order_count: number;
  not_linked_count: number;
  unverified_report_count: number;
  review_required_count: number;
  orders: CoverageOrder[];
  source: string;
  shipment_status_verified_by_po_link: false;
  inventory_posted_by_this_operation: false;
};

const STATUS_LABEL: Record<string, string> = {
  not_linked: 'No shipment journey recorded',
  linked_erp_recorded: 'Linked by ERP event — shipping status unknown',
  linked_staff_attested: 'Staff-attested link — review required',
  linked_reviewed_document: 'Reviewed-document link — arrival not verified',
  unverified_report_only: 'Reported in unverified source only',
  multiple_journeys_review_required: 'Linked to multiple journeys — allocation review required',
};

function message(reason: unknown) {
  return reason instanceof Error ? reason.message : 'Purchasing coverage unavailable';
}

/** Procurement visibility only. No event creation or PO receipt posting. */
export function PurchaseCoveragePanel() {
  const { role } = useAuth();
  const allowed = roleAllowed(role, PURCHASING_ROLES);
  const [data, setData] = useState<Coverage | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    if (!allowed) return;
    let active = true;
    setLoading(true);
    setError('');
    setData(null);
    api.get<Coverage>('/api/logistics/journey/purchasing-coverage')
      .then((result) => {
        if (!result || !Array.isArray(result.orders) ||
            result.shipment_status_verified_by_po_link !== false ||
            result.inventory_posted_by_this_operation !== false ||
            result.purchase_order_count !== result.orders.length ||
            result.orders.some((po) => (
              !po || typeof po.po_number !== 'string' ||
              !Array.isArray(po.linked_shipment_ids) ||
              !Array.isArray(po.reported_unverified_shipment_ids) ||
              po.shipping_status_verified !== false ||
              po.container_receipt_verified !== false ||
              po.warehouse_receipt_attributed_to_shipment !== false
            ))) {
          throw new Error('Purchasing coverage response is incomplete');
        }
        if (active) setData(result);
      })
      .catch((reason: unknown) => { if (active) setError(message(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [allowed, refresh]);

  if (!allowed) return null;
  return <section aria-label="Purchase orders awaiting shipment visibility"
    className="rounded-xl border border-slate-200 bg-white p-4 space-y-3">
    <div className="flex items-start justify-between flex-wrap gap-2">
      <div>
        <h3 className="font-semibold text-slate-900">Purchase orders awaiting shipment visibility</h3>
        <p className="text-xs text-slate-600 mt-1">
          Compares saved purchase orders to recorded shipment-journey events. It does not track carriers or verify warehouse arrival.
        </p>
      </div>
      <button type="button" disabled={loading} onClick={() => setRefresh((x) => x + 1)}
        className="rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:opacity-50">
        Refresh PO coverage
      </button>
    </div>
    {loading ? <p role="status" className="text-sm">Checking recorded purchasing data…</p> :
      error ? <p role="alert" className="text-sm text-red-700">PO coverage unavailable: {error}</p> :
      data && <>
        <p className="text-sm text-slate-700">
          <strong>{data.purchase_order_count}</strong> saved POs ·
          <strong> {data.not_linked_count}</strong> without a recorded shipment link ·
          <strong> {data.review_required_count}</strong> requiring review
        </p>
        {data.purchase_order_count === 0 ? <p className="text-sm text-slate-600">
          No saved POs in the connected purchasing ledger; this does not establish whether outside purchasing records exist.
        </p> : <ul className="space-y-2">
          {data.orders.map((po) => <li key={po.po_number} className="rounded-lg border border-slate-200 p-3 text-sm">
            <div className="flex flex-wrap justify-between gap-2">
              <span className="font-semibold text-slate-900">{po.po_number}</span>
              <span className="text-slate-700">{po.po_status}</span>
            </div>
            <p className="text-xs text-slate-700 mt-1">
              {STATUS_LABEL[po.shipment_link_status] || 'Shipment association requires investigation'}
            </p>
            {po.linked_shipment_ids.length > 0 && <p className="text-xs text-slate-600 mt-1">
              Recorded journeys: {po.linked_shipment_ids.join(', ')}
            </p>}
            {po.reported_unverified_shipment_ids.length > 0 && <p className="text-xs text-amber-800 mt-1">
              Unverified reports: {po.reported_unverified_shipment_ids.join(', ')}
            </p>}
          </li>)}
        </ul>}
        <p className="text-xs text-amber-900">
          A saved or fully received PO does not confirm a container's position, customs release,
          specific warehouse receipt, or accounting reconciliation.
        </p>
      </>}
  </section>;
}
