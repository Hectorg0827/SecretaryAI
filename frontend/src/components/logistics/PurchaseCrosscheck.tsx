import React, { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { useAuth } from '../../hooks/useAuth';
import { PURCHASING_ROLES, roleAllowed } from './logisticsShared';

type PurchaseCrosscheckResult = {
  shipment_id: string;
  status: string;
  po_results: Array<{
    po_number: string;
    status: string;
    ordered_cases?: string;
    received_cases_all_conditions?: string;
    remaining_cases?: string;
    posted_receipt_count?: number;
  }>;
  shared_po_numbers?: string[];
  posted_receipts_prove_shipment_received: boolean;
  shipment_inventory_reconciled: boolean;
  accounting_reconciled: boolean;
  review_required: boolean;
  note?: string;
};

const STATUS: Record<string, string> = {
  no_po_reference: 'No PO reference recorded in this shipment journey',
  missing_purchasing_record: 'A linked PO cannot be found in company purchasing records',
  shared_po_needs_allocation_review: 'PO is shared by multiple shipments — allocation review required',
  sku_quantity_difference: 'Shipment and PO product quantities differ',
  shipment_line_evidence_missing: 'Shipment quantities have not been recorded',
  po_quantities_match_not_receipt_attribution: 'PO quantities agree — container receipt still unverified',
};

function errorText(error: unknown) {
  return error instanceof Error ? error.message : 'Purchasing comparison unavailable';
}

/** Read-only comparison; never uses a PO-level posted receipt as container proof. */
export function PurchaseCrosscheck({ shipmentId }: { shipmentId: string }) {
  const { role } = useAuth();
  const allowed = roleAllowed(role, PURCHASING_ROLES);
  const [result, setResult] = useState<PurchaseCrosscheckResult | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!allowed) return;
    let current = true;
    setResult(null);
    setError('');
    setLoading(true);
    api.get<PurchaseCrosscheckResult>(
      `/api/logistics/journey/shipments/${encodeURIComponent(shipmentId)}/purchasing-crosscheck`,
    ).then((value) => {
      if (!value || !Array.isArray(value.po_results) ||
          value.posted_receipts_prove_shipment_received !== false ||
          value.shipment_inventory_reconciled !== false) {
        throw new Error('Purchasing comparison response is invalid');
      }
      if (current) setResult(value);
    }).catch((reason: unknown) => {
      if (current) setError(errorText(reason));
    }).finally(() => {
      if (current) setLoading(false);
    });
    return () => { current = false; };
  }, [shipmentId, allowed]);

  if (!allowed) return null;
  return <section aria-label="Purchase order crosscheck" className="rounded-xl border border-slate-200 p-4 space-y-3">
    <div>
      <h4 className="font-semibold text-slate-900">Purchase order and warehouse crosscheck</h4>
      <p className="text-xs text-slate-600 mt-1">
        Internal purchasing records, not carrier confirmation or QuickBooks reconciliation.
      </p>
    </div>
    {loading ? <p role="status" className="text-sm">Checking stored purchase orders…</p> :
      error ? <p role="alert" className="text-sm text-red-700">Purchasing crosscheck unavailable: {error}</p> :
      result && <>
        <p className="text-sm font-medium text-slate-800">{STATUS[result.status] || 'Purchase records require review'}</p>
        {result.po_results.length ? <ul className="space-y-2 text-sm">
          {result.po_results.map((row) => <li key={row.po_number} className="rounded-lg border border-slate-100 p-2">
            <span className="font-medium">{row.po_number}</span> · {row.status === 'not_found' ? 'Not found' : row.status}
            {row.ordered_cases != null && <p className="text-xs text-slate-700 mt-1">
              Ordered {row.ordered_cases} cases · Received {row.received_cases_all_conditions} (all conditions) ·
              Remaining {row.remaining_cases} · Posted receipt documents {row.posted_receipt_count}
            </p>}
          </li>)}
        </ul> : null}
        {!!result.shared_po_numbers?.length && <p className="text-sm text-amber-800">
          Shared PO(s): {result.shared_po_numbers.join(', ')}
        </p>}
        <p className="text-xs text-amber-900">
          A posted PO receipt does not prove which shipment delivered it. Shipment-level
          inventory and accounting reconciliation remain unverified.
        </p>
        {result.note && <p className="text-xs text-slate-600">{result.note}</p>}
      </>}
  </section>;
}
