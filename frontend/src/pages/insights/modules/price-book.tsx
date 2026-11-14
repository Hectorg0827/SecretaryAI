import { useEffect, useState } from 'react';
import { api } from '../../../lib/api';
import type { InsightMeta } from '../registry';
import { AdvisoryNotice, ErrorState, InsightShell } from '../ui';

export const meta: InsightMeta = {
  slug: 'price-book',
  title: 'Price book',
  description: 'Versioned case prices by SKU, package, market, channel and customer. Read-only in Phase 1.',
  group: 'Pricing',
  roles: ['owner', 'manager'],
};

type PriceEntry = {
  id: string;
  sku: string;
  package_label: string;
  market_code: string;
  channel: string;
  customer_ref: string;
  currency: string;
  list_price_per_case: string | number;
  effective_from: string;
  effective_to: string | null;
  revision: number;
  status: string;
  cost_snapshot_id: string | null;
  evidence_ref: string;
};

type PriceBookResponse = {
  items: PriceEntry[];
  book_state: 'unpopulated' | 'records_present';
  next_offset: number | null;
  price_changes_allowed: false;
};

export default function PriceBook() {
  const [data, setData] = useState<PriceBookResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    api.get<PriceBookResponse>('/api/insights/price-book')
      .then((response) => { if (active) setData(response); })
      .catch(() => {
        if (active) setError('Price-book data is unavailable. No approved prices can be inferred.');
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  return <InsightShell title={meta.title} description={meta.description}>
    <AdvisoryNotice source="connected_data" />
    <p className="text-sm text-slate-600">
      Draft registry for market-specific prices. Price approvals, customer-specific
      contracts, logistics reconciliation and publishing are not yet enabled.
    </p>
    {loading && <p role="status" className="text-sm text-slate-600">Checking price records…</p>}
    {error && <ErrorState message={error} />}
    {data && data.items.length === 0 && (
      <p role="status" className="rounded-lg bg-amber-50 p-4 text-sm text-amber-900">
        No price-book records have been entered. This does not mean products have no prices.
        Use the Price waterfall for preliminary cost scenarios.
      </p>
    )}
    {data && data.items.length > 0 && (
      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-slate-600"><tr>
            <th className="p-3">SKU / package</th><th className="p-3">Market / channel</th>
            <th className="p-3">Customer</th><th className="p-3">Price / case</th>
            <th className="p-3">Effective</th><th className="p-3">Status / source</th>
          </tr></thead>
          <tbody>
            {data.items.map((item) => (
              <tr key={item.id} className="border-t border-slate-100">
                <td className="p-3"><strong>{item.sku}</strong><div>{item.package_label}</div></td>
                <td className="p-3">{item.market_code} · {item.channel}</td>
                <td className="p-3">{item.customer_ref || 'General price'}</td>
                <td className="p-3 font-semibold">
                  {new Intl.NumberFormat('en-US', {
                    style: 'currency', currency: item.currency,
                  }).format(Number(item.list_price_per_case))}
                </td>
                <td className="p-3">{item.effective_from}{item.effective_to && ` – ${item.effective_to}`}</td>
                <td className="p-3">{item.status}
                  <div className="text-xs text-slate-500">
                    {item.cost_snapshot_id ? 'Cost snapshot linked' : 'No cost snapshot'}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.next_offset !== null && <p className="p-3 text-xs text-amber-800">
          Additional prices exist; full pagination is not yet available in the UI.
        </p>}
      </div>
    )}
  </InsightShell>;
}
