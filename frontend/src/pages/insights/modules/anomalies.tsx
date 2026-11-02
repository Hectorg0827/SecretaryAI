/** Unusual changes in orders and revenue, from GET /api/insights/anomalies (view_financials). */
import { useState } from 'react';
import type { InsightMeta } from '../registry';
import {
  AdvisoryNotice, ErrorState, InsightShell, LoadingState, useInsight, type AdvisoryFields,
} from '../ui';

export const meta: InsightMeta = {
  slug: 'anomalies',
  title: 'Unusual changes',
  description: 'Orders, customers and weeks that stand out from your normal pattern, and what to check.',
  group: 'Operations',
  // view_financials (revenue figures): owner and manager only.
  roles: ['owner', 'manager'],
};

type Kind = 'customer_order' | 'weekly_revenue' | 'weekly_orders';

export interface AnomalyItem {
  kind: Kind;
  severity: 'high' | 'medium' | 'low';
  direction: 'high' | 'low';
  value: number;
  usual_low: number;
  usual_high: number;
  suggested_check: string;
  customer?: string;
  customer_id?: string;
  order_id?: string;
  date?: string;
  period_start?: string;
  period_end?: string;
}

export interface AnomaliesResponse extends AdvisoryFields {
  as_of: string;
  lookback_days: number;
  weeks_compared: number;
  anomalies: AnomalyItem[];
  counts: { high: number; medium: number };
  orders_analyzed: number;
  customers_checked: number;
  customers_with_too_few_orders: number;
  weekly_trends: 'checked' | 'withheld';
  rows_skipped: number;
}

const WINDOWS = [
  { days: 60, label: 'Last 60 days' },
  { days: 90, label: 'Last 90 days' },
  { days: 180, label: 'Last 6 months' },
  { days: 365, label: 'Last 12 months' },
];

const SEVERITY_CFG: Record<string, { label: string; badge: string }> = {
  high:   { label: 'Needs attention', badge: 'bg-red-50 text-red-700 border-red-200' },
  medium: { label: 'Worth a look',    badge: 'bg-amber-50 text-amber-700 border-amber-200' },
  low:    { label: 'Minor',           badge: 'bg-slate-50 text-slate-600 border-slate-200' },
};

function fmtAmount(value: number): string {
  return value.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function fmtDate(iso?: string): string {
  if (!iso) return '—';
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function describe(item: AnomalyItem): { title: string; detail: string } {
  const range = `${fmtAmount(item.usual_low)}–${fmtAmount(item.usual_high)}`;
  const higher = item.direction === 'high';
  const week = `the week of ${fmtDate(item.period_start)}–${fmtDate(item.period_end)}`;
  if (item.kind === 'customer_order') {
    return {
      title: `${item.customer} placed an unusually ${higher ? 'large' : 'small'} order`,
      detail: `Order ${item.order_id} on ${fmtDate(item.date)} was ${fmtAmount(item.value)}. Their usual orders are about ${range}.`,
    };
  }
  if (item.kind === 'weekly_revenue') {
    return {
      title: `Sales were unusually ${higher ? 'high' : 'low'} in ${week}`,
      detail: `Invoiced that week: ${fmtAmount(item.value)}. A usual week is about ${range}.`,
    };
  }
  return {
    title: `Unusually ${higher ? 'many' : 'few'} orders in ${week}`,
    detail: `${fmtAmount(item.value)} orders that week. A usual week has about ${range}.`,
  };
}

function AnomaliesView({ data }: { data: AnomaliesResponse }) {
  return (
    <div className="space-y-4">
      <div className="grid gap-3 grid-cols-2 sm:grid-cols-4">
        {[
          [data.counts.high, 'Need attention'],
          [data.counts.medium, 'Worth a look'],
          [data.orders_analyzed, 'Orders checked'],
          [data.customers_checked, 'Customers compared'],
        ].map(([value, label]) => (
          <div key={label} className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="text-2xl font-bold text-slate-900">{value}</div>
            <div className="text-xs text-slate-500 mt-1">{label}</div>
          </div>
        ))}
      </div>

      {data.weekly_trends === 'withheld' && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Weekly sales and order totals were not checked: {data.rows_skipped} order(s) could not be read,
          and a missing order would make a week look lower than it really was.
        </div>
      )}

      {data.anomalies.length === 0 ? (
        <div className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-500">
          Nothing unusual found in the last {data.lookback_days} days.
        </div>
      ) : (
        <ul className="space-y-3" aria-label="Unusual changes found">
          {data.anomalies.map((item, index) => {
            const { title, detail } = describe(item);
            const severity = SEVERITY_CFG[item.severity] ?? SEVERITY_CFG.low;
            return (
              <li key={`${item.kind}-${item.order_id ?? item.period_end}-${index}`} className="rounded-xl border border-slate-200 bg-white p-4 space-y-1">
                <div className="flex items-start justify-between gap-3">
                  <div className="text-sm font-semibold text-slate-800">{title}</div>
                  <span className={`inline-block flex-shrink-0 rounded-full border px-2 py-0.5 text-xs font-medium ${severity.badge}`}>
                    {severity.label}
                  </span>
                </div>
                <p className="text-sm text-slate-600">{detail}</p>
                <p className="text-sm text-slate-700"><span className="font-medium">What to check:</span> {item.suggested_check}</p>
              </li>
            );
          })}
        </ul>
      )}

      <p className="text-xs text-slate-400">
        Each customer’s orders are compared with their own recent orders, and each full week with the other
        {` ${data.weeks_compared}`} weeks. Customers with fewer than four orders in the period
        ({data.customers_with_too_few_orders}) are not compared. Amounts are as invoiced in QuickBooks.
      </p>
    </div>
  );
}

export default function Anomalies() {
  const [days, setDays] = useState(90);
  const { data, error, loading, reload } = useInsight<AnomaliesResponse>(`/api/insights/anomalies?lookback_days=${days}`);
  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source={data?.source} />
      <div className="flex items-center gap-2">
        <label htmlFor="anomalies-window" className="text-sm text-slate-600">Period</label>
        <select
          id="anomalies-window"
          value={days}
          onChange={(event) => setDays(Number(event.target.value))}
          className="rounded-lg border border-slate-200 px-2 py-1 text-sm"
        >
          {WINDOWS.map((w) => <option key={w.days} value={w.days}>{w.label}</option>)}
        </select>
      </div>
      {loading && <LoadingState />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && !loading && <AnomaliesView data={data} />}
    </InsightShell>
  );
}
