import { useState, type FormEvent } from 'react';
import { api } from '../../../lib/api';
import type { InsightMeta } from '../registry';
import {
  AdvisoryNotice, ErrorState, InsightShell, LoadingState, useInsight, type AdvisoryFields,
} from '../ui';

export const meta: InsightMeta = {
  slug: 'brand-scorecards',
  title: 'Brand scorecards',
  description: 'How each product is selling: growth, customer reach, repeat buyers, returns, margin and stock cover.',
  group: 'Growth',
  // Backend permission view_financials admits owner and manager only.
  roles: ['owner', 'manager'],
};

interface ProductScore {
  item_id: string;
  name: string;
  sku: string;
  net_sales: number | null;
  net_cases: number | null;
  net_case_growth_pct: number | null;
  account_penetration_pct: number | null;
  repeat_account_pct: number | null;
  return_rate_pct: number | null;
  gross_margin_pct: number | null;
  weeks_of_supply: number | null;
  gaps: string[];
  review_status: string;
}

interface Period { start: string; end: string }

interface BrandScorecardResponse extends AdvisoryFields {
  weeks: number;
  current_period: Period;
  previous_period: Period;
  products: ProductScore[];
  products_total: number;
  rows_skipped: number;
}

interface SupplierScore {
  supplier_name: string;
  fill_rate_pct: number | null;
  on_time_pct: number | null;
  damage_rate_pct: number | null;
  composite_score: number | null;
  documented_inputs: boolean;
  gaps: string[];
}

interface SupplierResponse extends AdvisoryFields {
  suppliers: SupplierScore[];
}

const GAP_LABELS: Record<string, string> = {
  insufficient_comparable_history: 'No sales in the earlier period to compare',
  unavailable_net_sales_margin: 'No sales in this period',
  unavailable_weeks_of_supply: 'No recent sales to measure stock cover',
  unavailable_account_penetration: 'No active customers in this period',
  unavailable_repeat_account_rate: 'No buyers in this period',
  unavailable_return_rate: 'Nothing sold in this period',
  unavailable_product_cost: 'Product cost missing in QuickBooks',
  unavailable_stock_reading: 'Stock count unavailable',
  missing_source_revision: 'Source record not identified',
  missing_supplier_denominator: 'Not enough figures for an overall score',
};

const WEEK_OPTIONS = [4, 8, 13] as const;

const pct = (value: number | null) => (value == null ? '—' : `${value.toFixed(1)}%`);
const num = (value: number | null) => (value == null ? '—' : value.toLocaleString());
const money = (value: number | null) =>
  value == null ? '—' : value.toLocaleString(undefined, { style: 'currency', currency: 'USD' });
const gapText = (gaps: string[]) => gaps.map((gap) => GAP_LABELS[gap] ?? gap.replace(/_/g, ' ')).join('; ');

function ProductTable({ data }: { data: BrandScorecardResponse }) {
  if (data.products.length === 0) {
    return <p className="text-sm text-slate-500">No products with stock or sales in this period.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-xs text-slate-500">
          <tr>
            <th className="px-4 py-2 text-left font-medium">Product</th>
            <th className="px-4 py-2 text-right font-medium">Sales</th>
            <th className="px-4 py-2 text-right font-medium">Cases sold (net)</th>
            <th className="px-4 py-2 text-right font-medium">Change vs earlier period</th>
            <th className="px-4 py-2 text-right font-medium">Customers buying it</th>
            <th className="px-4 py-2 text-right font-medium">Repeat buyers</th>
            <th className="px-4 py-2 text-right font-medium">Returns</th>
            <th className="px-4 py-2 text-right font-medium">Gross margin</th>
            <th className="px-4 py-2 text-right font-medium">Weeks of stock</th>
            <th className="px-4 py-2 text-left font-medium">Notes</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {data.products.map((row) => (
            <tr key={row.item_id} className="hover:bg-slate-50">
              <td className="px-4 py-2.5">
                <div className="font-semibold text-slate-800">{row.name || row.item_id}</div>
                {row.sku && <div className="text-xs text-slate-400 font-mono">{row.sku}</div>}
              </td>
              <td className="px-4 py-2.5 text-right text-slate-700">{money(row.net_sales)}</td>
              <td className="px-4 py-2.5 text-right text-slate-700">{num(row.net_cases)}</td>
              <td className={`px-4 py-2.5 text-right font-medium ${
                row.net_case_growth_pct == null ? 'text-slate-400'
                  : row.net_case_growth_pct < 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                {row.net_case_growth_pct == null ? '—'
                  : `${row.net_case_growth_pct > 0 ? '+' : ''}${row.net_case_growth_pct.toFixed(1)}%`}
              </td>
              <td className="px-4 py-2.5 text-right text-slate-700">{pct(row.account_penetration_pct)}</td>
              <td className="px-4 py-2.5 text-right text-slate-700">{pct(row.repeat_account_pct)}</td>
              <td className="px-4 py-2.5 text-right text-slate-700">{pct(row.return_rate_pct)}</td>
              <td className="px-4 py-2.5 text-right text-slate-700">{pct(row.gross_margin_pct)}</td>
              <td className="px-4 py-2.5 text-right text-slate-700">{num(row.weeks_of_supply)}</td>
              <td className="px-4 py-2.5 text-xs text-slate-500">{gapText(row.gaps)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const SUPPLIER_FIELDS = [
  { key: 'units_ordered', label: 'Units ordered' },
  { key: 'units_filled', label: 'Units shipped' },
  { key: 'units_delivered', label: 'Units received' },
  { key: 'units_damaged', label: 'Units damaged' },
  { key: 'on_time_shipments', label: 'Shipments on time' },
  { key: 'received_shipments', label: 'Shipments received' },
] as const;

type SupplierField = typeof SUPPLIER_FIELDS[number]['key'];
type SupplierRow = Record<SupplierField | 'supplier_name' | 'evidence_reference' | 'evidence_version', string>;

const emptySupplier = (): SupplierRow => ({
  supplier_name: '', units_ordered: '', units_filled: '', units_delivered: '',
  units_damaged: '', on_time_shipments: '', received_shipments: '', evidence_reference: '',
  evidence_version: '',
});

function SupplierScorecards() {
  const [rows, setRows] = useState<SupplierRow[]>([emptySupplier()]);
  const [result, setResult] = useState<SupplierResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const update = (index: number, key: keyof SupplierRow, value: string) => {
    setRows((current) => current.map((row, i) => (i === index ? { ...row, [key]: value } : row)));
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const suppliers = [];
    for (const row of rows) {
      const figures: Record<string, number> = {};
      for (const { key } of SUPPLIER_FIELDS) {
        const value = Number(row[key]);
        if (row[key].trim() === '' || !Number.isFinite(value)) {
          setError('Fill in every number for each supplier.');
          return;
        }
        figures[key] = value;
      }
      if (!row.supplier_name.trim()) {
        setError('Give each supplier a name.');
        return;
      }
      suppliers.push({
        supplier_name: row.supplier_name.trim(),
        ...figures,
        evidence_reference: row.evidence_reference.trim(),
        evidence_version: row.evidence_version.trim(),
      });
    }
    setError(null);
    setSubmitting(true);
    api.post<SupplierResponse>('/api/insights/brand-scorecards/supplier-preview', { suppliers })
      .then((response) => setResult(response))
      .catch((err: unknown) => {
        setResult(null);
        setError(err instanceof Error ? err.message : 'Could not score these suppliers.');
      })
      .finally(() => setSubmitting(false));
  };

  return (
    <section className="space-y-3" aria-label="Supplier scorecards">
      <div>
        <h2 className="text-base font-semibold text-slate-800">Supplier scorecards</h2>
        <p className="text-xs text-slate-500">
          Your connected systems don&apos;t record deliveries, so enter each supplier&apos;s figures from your receiving records.
        </p>
      </div>
      <form onSubmit={submit} className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
        {rows.map((row, index) => (
          <fieldset key={index} className="grid gap-2 sm:grid-cols-3 lg:grid-cols-4 border-b border-slate-100 pb-3">
            <legend className="text-xs font-semibold text-slate-500">Supplier {index + 1}</legend>
            <label className="text-xs text-slate-600">Supplier name
              <input name={`supplier_name_${index}`} value={row.supplier_name}
                onChange={(e) => update(index, 'supplier_name', e.target.value)} maxLength={120}
                className="mt-1 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
            </label>
            {SUPPLIER_FIELDS.map(({ key, label }) => (
              <label key={key} className="text-xs text-slate-600">{label}
                <input name={`${key}_${index}`} type="number" min="0" step="any" value={row[key]}
                  onChange={(e) => update(index, key, e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
              </label>
            ))}
            <label className="text-xs text-slate-600">Where these figures come from (optional)
              <input name={`evidence_reference_${index}`} value={row.evidence_reference}
                onChange={(e) => update(index, 'evidence_reference', e.target.value)} maxLength={120}
                placeholder="e.g. Receiving log"
                className="mt-1 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
            </label>
            <label className="text-xs text-slate-600">Date or version of that record (optional)
              <input name={`evidence_version_${index}`} value={row.evidence_version}
                onChange={(e) => update(index, 'evidence_version', e.target.value)} maxLength={60}
                placeholder="e.g. October 2026"
                className="mt-1 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
            </label>
          </fieldset>
        ))}
        <div className="flex gap-2">
          {rows.length < 25 && (
            <button type="button" onClick={() => setRows((current) => [...current, emptySupplier()])}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50">
              Add supplier
            </button>
          )}
          <button type="submit" disabled={submitting}
            className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-700 disabled:opacity-50">
            {submitting ? 'Scoring…' : 'Score suppliers'}
          </button>
        </div>
      </form>
      {error && <ErrorState message={error} />}
      {result && (
        <>
          <AdvisoryNotice source={result.source} />
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500">
                <tr>
                  <th className="px-4 py-2 text-left font-medium">Supplier</th>
                  <th className="px-4 py-2 text-right font-medium">Order filled</th>
                  <th className="px-4 py-2 text-right font-medium">On time</th>
                  <th className="px-4 py-2 text-right font-medium">Damaged</th>
                  <th className="px-4 py-2 text-right font-medium">Overall score</th>
                  <th className="px-4 py-2 text-left font-medium">Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {result.suppliers.map((row) => (
                  <tr key={row.supplier_name}>
                    <td className="px-4 py-2.5 font-semibold text-slate-800">{row.supplier_name}</td>
                    <td className="px-4 py-2.5 text-right text-slate-700">{pct(row.fill_rate_pct)}</td>
                    <td className="px-4 py-2.5 text-right text-slate-700">{pct(row.on_time_pct)}</td>
                    <td className="px-4 py-2.5 text-right text-slate-700">{pct(row.damage_rate_pct)}</td>
                    <td className="px-4 py-2.5 text-right font-semibold text-slate-800">
                      {row.composite_score == null ? '—' : `${row.composite_score.toFixed(1)} / 100`}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-slate-500">
                      {row.gaps.includes('missing_source_revision')
                        ? 'Add where the figures come from and its date to get an overall score.'
                        : gapText(row.gaps)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

export default function BrandScorecards() {
  const [weeks, setWeeks] = useState<number>(4);
  const { data, error, loading, reload } = useInsight<BrandScorecardResponse>(
    `/api/insights/brand-scorecards?weeks=${weeks}`,
  );

  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source="connected_data" />
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="text-slate-600">
          Compare the last{' '}
          <select value={weeks} onChange={(e) => setWeeks(Number(e.target.value))}
            className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-sm">
            {WEEK_OPTIONS.map((option) => <option key={option} value={option}>{option} weeks</option>)}
          </select>{' '}
          with the period before
        </label>
        {data && (
          <span className="text-xs text-slate-400">
            {data.current_period.start} to {data.current_period.end} vs {data.previous_period.start} to {data.previous_period.end}
          </span>
        )}
      </div>
      <p className="text-xs text-slate-500">
        QuickBooks doesn&apos;t record brands, so each product is scored on its own. Gross margin uses the product cost in QuickBooks.
      </p>
      {loading && <LoadingState />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && !loading && (
        <>
          <ProductTable data={data} />
          {data.rows_skipped > 0 && (
            <p className="text-xs text-amber-700">
              {data.rows_skipped} record(s) could not be read and were left out of these numbers.
            </p>
          )}
        </>
      )}
      <SupplierScorecards />
    </InsightShell>
  );
}
