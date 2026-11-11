/**
 * "Draft PO" form: choose the vendor, quantity and unit cost for one item.
 *
 * The vendor list, the item's cost and a suggested quantity come from
 * QuickBooks. With all three filled in, approving the draft creates the order
 * in QuickBooks; without a vendor or cost it is still drafted, and the form
 * says plainly that it will then have to be entered by hand.
 */
import React, { useEffect, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api, PurchaseOrderOptions } from '../../lib/api';

export function DraftPOModal({ itemId, productName, onClose }: {
  itemId: string; productName: string; onClose: () => void;
}) {
  const [options, setOptions] = useState<PurchaseOrderOptions | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [vendorId, setVendorId] = useState('');
  const [qty, setQty] = useState('');
  const [cost, setCost] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.purchaseOrders.options(itemId)
      .then((o) => {
        if (cancelled) return;
        setOptions(o);
        setVendorId(o.preferred_vendor_id ?? '');
        setQty(String(o.item?.suggested_qty ?? 1));
        setCost(o.item && o.item.unit_cost > 0 ? String(o.item.unit_cost) : '');
      })
      .catch(() => { if (!cancelled) setLoadError(true); });
    return () => { cancelled = true; };
  }, [itemId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const quantity = Number(qty);
  const unitCost = Number(cost);
  const quantityOk = Number.isFinite(quantity) && quantity > 0;
  const complete = !!vendorId && quantityOk && Number.isFinite(unitCost) && unitCost > 0;
  const total = complete ? quantity * unitCost : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!quantityOk) { toast.error('Enter a quantity greater than zero.'); return; }
    setSaving(true);
    try {
      const res = await api.purchaseOrders.draft({
        item_id: itemId,
        product_name: productName,
        vendor_id: vendorId || null,
        qty: quantity,
        ...(Number.isFinite(unitCost) && unitCost > 0 ? { unit_cost: unitCost } : {}),
      });
      toast.success(res.message, { duration: 6000 });
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not create the purchase order draft');
    } finally {
      setSaving(false);
    }
  };

  const field = 'w-full text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-400';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-labelledby="draft-po-title"
        className="bg-white rounded-2xl shadow-2xl w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 id="draft-po-title" className="text-base font-bold text-slate-900">Draft purchase order</h2>
            <p className="text-sm text-slate-500">{options?.item?.name || productName}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
        </div>

        {!options && !loadError && (
          <p className="flex items-center gap-2 text-sm text-slate-400 py-6"><Loader2 size={14} className="animate-spin" /> Loading from QuickBooks…</p>
        )}

        {(options || loadError) && (
          <form onSubmit={submit} className="space-y-4">
            {loadError && (
              <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                We could not reach QuickBooks for vendors and costs. You can still create the draft; it will need to be entered in QuickBooks by hand.
              </p>
            )}
            <div>
              <label htmlFor="po-vendor" className="block text-xs font-semibold text-slate-600 mb-1">Vendor</label>
              <select id="po-vendor" value={vendorId} onChange={(e) => setVendorId(e.target.value)} className={field}>
                <option value="">Choose a vendor…</option>
                {(options?.vendors ?? []).map((v) => (
                  <option key={v.id} value={v.id}>{v.name}{v.id === options?.preferred_vendor_id ? ' (usual supplier)' : ''}</option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="po-qty" className="block text-xs font-semibold text-slate-600 mb-1">Quantity</label>
                <input id="po-qty" type="number" min="1" step="any" required value={qty} onChange={(e) => setQty(e.target.value)} className={field} />
                {options?.item && options.item.weekly_sell_rate > 0 && (
                  <p className="text-xs text-slate-400 mt-1">Suggested for about 8 weeks of stock ({options.item.on_hand} on hand).</p>
                )}
              </div>
              <div>
                <label htmlFor="po-cost" className="block text-xs font-semibold text-slate-600 mb-1">Unit cost ($)</label>
                <input id="po-cost" type="number" min="0" step="0.01" value={cost} onChange={(e) => setCost(e.target.value)} className={field} />
              </div>
            </div>

            <div className={`text-sm rounded-lg px-3 py-2 border ${complete ? 'bg-emerald-50 border-emerald-200 text-emerald-800' : 'bg-amber-50 border-amber-200 text-amber-800'}`}>
              {complete
                ? <>Total <strong>${total!.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong>. Once a manager or owner approves it, this order is created in QuickBooks.</>
                : <>Without a vendor and a unit cost this draft cannot be created in QuickBooks automatically — whoever approves it will be asked to enter it by hand.</>}
            </div>

            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={onClose} className="px-4 py-2 text-sm font-medium text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50">Cancel</button>
              <button type="submit" disabled={saving || !quantityOk}
                className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-50">
                {saving && <Loader2 size={14} className="animate-spin" />}
                Send for approval
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
