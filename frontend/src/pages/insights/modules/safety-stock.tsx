import { useState, type FormEvent } from 'react';
import clsx from 'clsx';
import { api } from '../../../lib/api';
import type { InsightMeta } from '../registry';
import {
  AdvisoryNotice, ErrorState, InsightShell, LoadingState, useInsight, type AdvisoryFields,
} from '../ui';

// Backend: GET/POST /api/insights/safety-stock require view_inventory.
export const meta: InsightMeta = {
  slug: 'safety-stock',
  title: 'Safety stock & reorder points',
  description: 'How much buffer stock to keep and when to reorder, allowing for uneven sales and late deliveries.',
  group: 'Supply chain',
  roles: ['owner', 'manager', 'sales_rep', 'back_office'],
};

interface Recommendation {
  safety_stock_units: number;
  reorder_point_units: number;
  suggested_order_qty: number;
  stockout_risk_without_buffer: number;
  avg_daily_sales: number;
  lead_time_days_avg: number;
  data_quality: 'good' | 'sparse' | 'insufficient';
  notes: string[];
  reorder_now: boolean;
  reorder_reason: string;
}

interface StockRow extends Partial<Recommendation> {
  item_id: string | null;
  product_name: string;
  sku: string | null;
  status: 'calculated' | 'insufficient_data';
  reason: string;
  on_hand: number | null;
  on_order: number;
  stock_position?: number;
  reorder_now: boolean;
}

interface SafetyStockResponse extends AdvisoryFields {
  items: StockRow[];
  summary: {
    items_total: number;
    items_calculated: number;
    items_insufficient_data: number;
    items_to_reorder: number;
    rows_skipped: number;
  };
}

interface PreviewResponse extends AdvisoryFields, Recommendation {
  product_name: string;
}

const SERVICE_LEVELS = [
  { value: '0.9', label: '90% — fewer stockouts accepted' },
  { value: '0.95', label: '95% — usual choice' },
  { value: '0.98', label: '98%' },
  { value: '0.99', label: '99% — almost never run out' },
];

const QUALITY_LABEL: Record<Recommendation['data_quality'], string> = {
  good: 'Good history',
  sparse: 'Limited history',
  insufficient: 'Very little history',
};

const inputClass = 'w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-300';

function units(value: number | null | undefined) {
  return value == null ? '—' : value.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function pct(value: number | undefined) {
  return value == null ? '—' : `${Math.round(value * 100)}%`;
}

function parseList(text: string): number[] | null {
  const parts = text.split(/[\s,;]+/).filter(Boolean);
  const numbers = parts.map(Number);
  return parts.length && numbers.every((n) => Number.isFinite(n)) ? numbers : null;
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-xl font-bold text-slate-900 mt-1">{value}</div>
    </div>
  );
}

function ItemsTable({ items }: { items: StockRow[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-slate-500">No inventory items were found in your connected system.</p>;
  }
  return (
    <div className="rounded-xl border border-slate-200 bg-white overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-xs text-slate-500 text-left">
          <tr>
            <th className="px-4 py-2 font-medium">Product</th>
            <th className="px-4 py-2 font-medium">In stock + on order</th>
            <th className="px-4 py-2 font-medium">Reorder when stock reaches</th>
            <th className="px-4 py-2 font-medium">Safety buffer</th>
            <th className="px-4 py-2 font-medium">Suggested order size</th>
            <th className="px-4 py-2 font-medium">Sales / day · Supplier days</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {items.map((item) => (
            <tr key={`${item.item_id ?? ''}-${item.sku ?? item.product_name}`} className="align-top">
              <td className="px-4 py-3">
                <div className="font-semibold text-slate-800">{item.product_name || item.sku}</div>
                {item.sku && <div className="text-xs text-slate-400 font-mono">{item.sku}</div>}
                {item.reorder_now && (
                  <span className="inline-block mt-1 text-xs font-semibold px-2 py-0.5 rounded-lg bg-amber-100 text-amber-800">
                    Time to reorder
                  </span>
                )}
              </td>
              {item.status === 'insufficient_data' ? (
                <td colSpan={5} className="px-4 py-3 text-slate-500">
                  <span className="font-medium text-slate-600">Not enough data. </span>{item.reason}
                </td>
              ) : (
                <>
                  <td className="px-4 py-3 text-slate-700">
                    {units(item.on_hand)} + {units(item.on_order)}
                  </td>
                  <td className="px-4 py-3 font-semibold text-slate-800">{units(item.reorder_point_units)}</td>
                  <td className="px-4 py-3 text-slate-700">{units(item.safety_stock_units)}</td>
                  <td className="px-4 py-3 text-slate-700">{units(item.suggested_order_qty)}</td>
                  <td className="px-4 py-3 text-slate-600">
                    {units(item.avg_daily_sales)} · {units(item.lead_time_days_avg)} days
                    {item.data_quality && (
                      <div className={clsx('text-xs mt-1', item.data_quality === 'good' ? 'text-emerald-600' : 'text-amber-700')}>
                        {QUALITY_LABEL[item.data_quality]}
                      </div>
                    )}
                  </td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PreviewForm() {
  const [form, setForm] = useState({
    product_name: '', daily_sales: '', lead_times_days: '', unit_cost: '', current_stock: '', service_level: '0.95',
  });
  const [result, setResult] = useState<PreviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (field: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((prev) => ({ ...prev, [field]: event.target.value }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const dailySales = parseList(form.daily_sales);
    const leadTimes = parseList(form.lead_times_days);
    const unitCost = Number(form.unit_cost);
    const stock = Number(form.current_stock);
    if (!form.product_name.trim() || !dailySales || !leadTimes || form.unit_cost === '' || form.current_stock === ''
        || !Number.isFinite(unitCost) || !Number.isFinite(stock)) {
      setError('Fill in every field; use numbers separated by commas for the lists.');
      return;
    }
    if (dailySales.length < 7) {
      setError('Enter at least 7 days of sales.');
      return;
    }
    setBusy(true);
    setError(null);
    api.post<PreviewResponse>('/api/insights/safety-stock/preview', {
      product_name: form.product_name.trim(),
      daily_sales: dailySales,
      lead_times_days: leadTimes,
      unit_cost: unitCost,
      current_stock: stock,
      service_level: Number(form.service_level),
    })
      .then(setResult)
      .catch((err: unknown) => {
        setResult(null);
        setError(err instanceof Error ? err.message : 'The calculation failed.');
      })
      .finally(() => setBusy(false));
  };

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 space-y-4" aria-label="Try your own numbers">
      <div>
        <h2 className="text-sm font-semibold text-slate-800">Try your own numbers</h2>
        <p className="text-xs text-slate-500 mt-1">
          For a product without enough history here, enter what you know. Nothing is saved.
        </p>
      </div>
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-slate-600 space-y-1">
          <span>Product</span>
          <input name="product_name" className={inputClass} value={form.product_name} onChange={set('product_name')} />
        </label>
        <label className="text-xs text-slate-600 space-y-1">
          <span>How often you want stock on hand when customers order</span>
          <select name="service_level" className={inputClass} value={form.service_level} onChange={set('service_level')}>
            {SERVICE_LEVELS.map((level) => <option key={level.value} value={level.value}>{level.label}</option>)}
          </select>
        </label>
        <label className="text-xs text-slate-600 space-y-1 sm:col-span-2">
          <span>Units sold per day, recent days (e.g. 8, 12, 10, 9, 11, 10, 12)</span>
          <input name="daily_sales" className={inputClass} value={form.daily_sales} onChange={set('daily_sales')} />
        </label>
        <label className="text-xs text-slate-600 space-y-1 sm:col-span-2">
          <span>Days your supplier took to deliver, past orders (e.g. 10, 14, 12)</span>
          <input name="lead_times_days" className={inputClass} value={form.lead_times_days} onChange={set('lead_times_days')} />
        </label>
        <label className="text-xs text-slate-600 space-y-1">
          <span>Cost per unit ($)</span>
          <input name="unit_cost" inputMode="decimal" className={inputClass} value={form.unit_cost} onChange={set('unit_cost')} />
        </label>
        <label className="text-xs text-slate-600 space-y-1">
          <span>Units in stock now</span>
          <input name="current_stock" inputMode="decimal" className={inputClass} value={form.current_stock} onChange={set('current_stock')} />
        </label>
        <div className="sm:col-span-2">
          <button type="submit" disabled={busy} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-700 disabled:opacity-50">
            {busy ? 'Calculating…' : 'Calculate'}
          </button>
        </div>
      </form>
      {error && <ErrorState message={error} />}
      {result && (
        <div className="space-y-3" data-testid="preview-result">
          <AdvisoryNotice source={result.source} />
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat label="Reorder when stock reaches" value={units(result.reorder_point_units)} />
            <Stat label="Safety buffer" value={units(result.safety_stock_units)} />
            <Stat label="Suggested order size" value={units(result.suggested_order_qty)} />
          </div>
          <p className="text-sm text-slate-600">
            {result.reorder_now ? 'Reorder now. ' : ''}{result.reorder_reason} Chance of running out before a
            new order arrives, without a buffer: {pct(result.stockout_risk_without_buffer)}.
          </p>
          {result.notes.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-slate-500">
              {result.notes.map((note) => <li key={note}>{note}</li>)}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

export default function SafetyStockInsight() {
  const [serviceLevel, setServiceLevel] = useState('0.95');
  const { data, error, loading, reload } = useInsight<SafetyStockResponse>(
    `/api/insights/safety-stock?service_level=${serviceLevel}`,
  );

  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source={data?.source ?? 'connected_data'} />
      <label className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
        <span>Stock on hand when customers order:</span>
        <select
          name="overview_service_level"
          className="rounded-lg border border-slate-300 px-2 py-1 text-xs"
          value={serviceLevel}
          onChange={(event) => setServiceLevel(event.target.value)}
        >
          {SERVICE_LEVELS.map((level) => <option key={level.value} value={level.value}>{level.label}</option>)}
        </select>
      </label>
      {loading && <LoadingState />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && !loading && (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat label="Products checked" value={data.summary.items_total} />
            <Stat label="Time to reorder" value={data.summary.items_to_reorder} />
            <Stat label="Not enough data yet" value={data.summary.items_insufficient_data} />
          </div>
          <p className="text-xs text-slate-500">
            Based on the last 90 days of sales and how long your past purchase orders took to arrive.
            “On order” counts purchase orders recorded in SecretaryAI.
            {data.summary.rows_skipped > 0 && ` ${data.summary.rows_skipped} unreadable records were skipped.`}
          </p>
          <ItemsTable items={data.items} />
        </>
      )}
      <PreviewForm />
    </InsightShell>
  );
}
