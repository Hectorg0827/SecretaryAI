import React, { useEffect, useState } from 'react';
import clsx from 'clsx';
import { AlertTriangle, ClipboardList, Package, XCircle } from 'lucide-react';
import { api, InventoryAlert } from '../../lib/api';
import { BUTTON_SECONDARY, ErrorNote } from './logisticsShared';

const STATUS: Record<InventoryAlert['stock_status'], {
  label: string; badge: string; border: string; icon: React.ElementType; rank: number;
}> = {
  out_of_stock: { label: 'Out of stock', badge: 'bg-red-100 text-red-700',       border: 'border-l-red-500',    icon: XCircle,       rank: 0 },
  critical:     { label: 'Critical',     badge: 'bg-orange-100 text-orange-700', border: 'border-l-orange-400', icon: AlertTriangle, rank: 1 },
  low:          { label: 'Low',          badge: 'bg-amber-100 text-amber-700',   border: 'border-l-amber-400',  icon: Package,       rank: 2 },
};

/** Most urgent first: out of stock, then critical, then low; shortest runway first. */
export function sortReorderAlerts(alerts: InventoryAlert[]): InventoryAlert[] {
  const rank = (a: InventoryAlert) => STATUS[a.stock_status]?.rank ?? 3;
  return [...alerts].sort((a, b) =>
    rank(a) - rank(b)
    || (a.weeks_remaining ?? Infinity) - (b.weeks_remaining ?? Infinity)
    || a.product_name.localeCompare(b.product_name));
}

function runway(weeks: number | null): string {
  return weeks == null ? 'Unknown' : `~${weeks.toFixed(1)} wks`;
}

const SOURCE_NOTE =
  'Based on QuickBooks stock levels and recorded sales rates. Products without a recorded sales rate only appear here once they run out.';

/**
 * Products that need reordering, from GET /api/inventory/alerts. A failed read
 * is shown as an error, never as an empty queue.
 */
export function ReorderQueue({ onCountChange }: { onCountChange?: (count: number | null) => void }) {
  const [alerts, setAlerts] = useState<InventoryAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError(null);
    api.get<{ alerts: InventoryAlert[]; count: number }>('/api/inventory/alerts').then((data) => {
      if (!Array.isArray(data?.alerts)) throw new Error('The reorder list is unavailable.');
      if (current) setAlerts(sortReorderAlerts(data.alerts));
    }).catch((reason: unknown) => {
      if (!current) return;
      setAlerts([]);
      setError(reason ?? new Error('The reorder list is unavailable.'));
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [revision]);

  useEffect(() => {
    onCountChange?.(loading || error ? null : alerts.length);
  }, [alerts, loading, error, onCountChange]);

  return (
    <section className="p-4 sm:p-6 space-y-4" aria-label="Reorder queue">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold text-slate-900">Products to reorder</h2>
          <p className="text-sm text-slate-600 max-w-2xl">{SOURCE_NOTE}</p>
          <p className="text-sm text-slate-600 max-w-2xl">
            To order more, create a purchase order on the Purchase Orders tab.
          </p>
        </div>
        <button type="button" disabled={loading} onClick={() => setRevision((value) => value + 1)}
          className={BUTTON_SECONDARY}>Refresh</button>
      </div>

      {loading ? (
        <div className="space-y-2" role="status" aria-label="Loading reorder list">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-14 bg-slate-100 rounded-lg animate-pulse" />
          ))}
        </div>
      ) : error ? (
        <ErrorNote error={error} fallback="The reorder list is unavailable." />
      ) : alerts.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-slate-600 gap-3">
          <div className="w-14 h-14 rounded-2xl bg-slate-100 border border-slate-200 flex items-center justify-center">
            <ClipboardList className="w-6 h-6 text-slate-400" />
          </div>
          <p className="text-sm font-medium text-slate-700">No products are low or out of stock right now.</p>
        </div>
      ) : (
        <div className="overflow-x-auto border border-slate-200 rounded-xl">
          <table className="w-full">
            <thead className="bg-white border-b border-slate-100">
              <tr>
                {['Product', 'Status', 'Qty on Hand', 'Est. Runway', 'Needs a PO'].map((h) => (
                  <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {alerts.map((alert) => {
                const cfg = STATUS[alert.stock_status] ?? STATUS.low;
                const Icon = cfg.icon;
                return (
                  <tr key={alert.item_id || alert.product_name} className={clsx('border-l-4', cfg.border)}>
                    <td className="px-5 py-3.5">
                      <div className="text-sm font-semibold text-slate-800">{alert.product_name}</div>
                      <div className="text-xs text-slate-600 font-mono">{alert.item_id}</div>
                    </td>
                    <td className="px-5 py-3.5">
                      <span className={clsx('inline-flex items-center gap-1 text-xs font-semibold px-2 py-1 rounded-lg', cfg.badge)}>
                        <Icon size={11} />
                        {cfg.label}
                      </span>
                    </td>
                    <td className="px-5 py-3.5 text-sm text-slate-700">{Number(alert.total_qty).toLocaleString()} units</td>
                    <td className="px-5 py-3.5 text-sm text-slate-700">{runway(alert.weeks_remaining)}</td>
                    <td className="px-5 py-3.5 text-sm text-slate-700">{alert.needs_po ? 'Yes' : 'Not yet'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
