import { useState } from 'react';
import type { InsightMeta } from '../registry';
import {
  AdvisoryNotice, ErrorState, InsightShell, LoadingState, useInsight, type AdvisoryFields,
} from '../ui';

export const meta: InsightMeta = {
  slug: 'portfolio-review',
  title: 'Product range review',
  description: 'Which products to keep, which to fix and which to review for phase-out, with the money tied up in stock.',
  group: 'Growth',
  // Backend permission view_financials admits owner and manager only.
  roles: ['owner', 'manager'],
};

type Decision = 'keep' | 'fix' | 'review_phase_out' | 'check_data';

interface ProductRow {
  item_id: string;
  name: string;
  sku: string;
  decision: Decision;
  status: string;
  reasons: string[];
  net_sales: number | null;
  net_cases: number | null;
  contribution_margin_pct: number | null;
  weeks_of_supply: number | null;
  working_capital_tied: number | null;
  no_sales_in_period: boolean;
}

interface PortfolioResponse extends AdvisoryFields {
  weeks: number;
  period: { start: string; end: string };
  policy: { min_margin_pct: number; max_weeks_of_supply: number };
  summary: Record<Decision, number>;
  working_capital_in_phase_out_review: number | null;
  products: ProductRow[];
  rows_skipped: number;
  checks_not_applied: string[];
}

const DECISIONS: Record<Decision, { label: string; badge: string }> = {
  keep: { label: 'Keep', badge: 'bg-emerald-100 text-emerald-700' },
  fix: { label: 'Fix', badge: 'bg-amber-100 text-amber-700' },
  review_phase_out: { label: 'Review: reprice or phase out', badge: 'bg-red-100 text-red-700' },
  check_data: { label: 'Check the data', badge: 'bg-slate-100 text-slate-600' },
};

const REASONS: Record<string, string> = {
  low_contribution_margin: 'Margin below your target',
  excess_weeks_of_supply: 'Far more stock than recent sales need',
  aged_inventory: 'Stock is old',
  stockout_with_observed_demand: 'Out of stock while customers still order',
  dependent_skus_block_rationalization: 'Other products depend on it',
  missing_source_revision: 'Source record not identified',
  missing_net_sales: 'No sales in this period',
  missing_velocity: 'No sales to measure stock cover',
  missing_product_cost: 'Product cost missing in QuickBooks',
  missing_stock_reading: 'Stock count unavailable',
};

const WEEK_OPTIONS = [13, 26, 52] as const;

const money = (value: number | null) =>
  value == null ? '—' : value.toLocaleString(undefined, { style: 'currency', currency: 'USD' });
const pct = (value: number | null) => (value == null ? '—' : `${value.toFixed(1)}%`);

export default function PortfolioReview() {
  const [weeks, setWeeks] = useState<number>(13);
  const { data, error, loading, reload } = useInsight<PortfolioResponse>(
    `/api/insights/portfolio-review?weeks=${weeks}`,
  );

  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source="connected_data" />
      <label className="text-sm text-slate-600">
        Based on the last{' '}
        <select value={weeks} onChange={(e) => setWeeks(Number(e.target.value))}
          className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-sm">
          {WEEK_OPTIONS.map((option) => <option key={option} value={option}>{option} weeks</option>)}
        </select>{' '}
        of sales
      </label>
      {loading && <LoadingState />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && !loading && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {(Object.keys(DECISIONS) as Decision[]).map((decision) => (
              <div key={decision} className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="text-xs text-slate-500">{DECISIONS[decision].label}</div>
                <div className="text-2xl font-bold text-slate-900">{data.summary[decision] ?? 0}</div>
                {decision === 'review_phase_out' && (
                  <div className="text-xs text-slate-500 mt-1">
                    {money(data.working_capital_in_phase_out_review)} tied up in their stock
                  </div>
                )}
              </div>
            ))}
          </div>
          <p className="text-xs text-slate-500">
            Margin target {data.policy.min_margin_pct}% and at most {data.policy.max_weeks_of_supply} weeks of stock.
            Stock age and product dependencies are not in your connected systems, so they are not checked here.
          </p>
          {data.products.length === 0 ? (
            <p className="text-sm text-slate-500">No products with stock or sales in this period.</p>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-xs text-slate-500">
                  <tr>
                    <th className="px-4 py-2 text-left font-medium">Product</th>
                    <th className="px-4 py-2 text-left font-medium">Suggestion</th>
                    <th className="px-4 py-2 text-left font-medium">Why</th>
                    <th className="px-4 py-2 text-right font-medium">Sales</th>
                    <th className="px-4 py-2 text-right font-medium">Margin after product cost</th>
                    <th className="px-4 py-2 text-right font-medium">Weeks of stock</th>
                    <th className="px-4 py-2 text-right font-medium">Money tied up in stock</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.products.map((row) => (
                    <tr key={row.item_id} className="hover:bg-slate-50">
                      <td className="px-4 py-2.5">
                        <div className="font-semibold text-slate-800">{row.name || row.item_id}</div>
                        {row.sku && <div className="text-xs text-slate-400 font-mono">{row.sku}</div>}
                      </td>
                      <td className="px-4 py-2.5">
                        <span className={`text-xs font-semibold px-2 py-1 rounded-lg ${DECISIONS[row.decision].badge}`}>
                          {DECISIONS[row.decision].label}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-xs text-slate-500">
                        {row.reasons.length === 0 ? 'Healthy margin and stock cover'
                          : row.reasons.map((reason) => REASONS[reason] ?? reason.replace(/_/g, ' ')).join('; ')}
                      </td>
                      <td className="px-4 py-2.5 text-right text-slate-700">{money(row.net_sales)}</td>
                      <td className="px-4 py-2.5 text-right text-slate-700">{pct(row.contribution_margin_pct)}</td>
                      <td className="px-4 py-2.5 text-right text-slate-700">
                        {row.weeks_of_supply == null ? '—' : row.weeks_of_supply.toLocaleString()}
                      </td>
                      <td className="px-4 py-2.5 text-right text-slate-700">{money(row.working_capital_tied)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {data.rows_skipped > 0 && (
            <p className="text-xs text-amber-700">
              {data.rows_skipped} record(s) could not be read and were left out.
            </p>
          )}
        </>
      )}
    </InsightShell>
  );
}
