import { useState, type FormEvent } from 'react';
import clsx from 'clsx';
import { api } from '../../../lib/api';
import type { InsightMeta } from '../registry';
import {
  AdvisoryNotice, ErrorState, InsightShell, LoadingState, useInsight, type AdvisoryFields,
} from '../ui';

// Backend: GET/POST /api/insights/pricing-suggestions require view_financials.
export const meta: InsightMeta = {
  slug: 'pricing-suggestions',
  title: 'Price suggestions',
  description: 'Suggested prices based on your margins and how customers reacted to past price changes. Advisory only.',
  group: 'Pricing',
  roles: ['owner', 'manager'],
};

interface Suggestion {
  status: 'suggested_change' | 'hold';
  current_price: number;
  suggested_price: number;
  change_pct: number;
  current_margin_pct: number;
  projected_margin_pct: number;
  below_target_margin: boolean;
  volume_impact_pct: number;
  revenue_impact_annual: number;
  rationale: string;
  price_changes_observed: number;
  price_sensitivity: string;
}

interface ItemRow extends Partial<Omit<Suggestion, 'status'>> {
  item_id: string | null;
  product_name: string;
  sku: string | null;
  status: Suggestion['status'] | 'insufficient_data';
  reason: string;
}

interface PricingResponse extends AdvisoryFields {
  target_margin_pct: number;
  items: ItemRow[];
  summary: {
    items_total: number;
    suggested_changes: number;
    holds: number;
    below_target_margin: number;
    items_insufficient_data: number;
    rows_skipped: number;
  };
}

// The advisory stamp owns the top-level "status", so a price check reports "outcome".
interface PriceCheckResponse extends AdvisoryFields, Omit<Suggestion, 'status'> {
  product_name: string;
  outcome: Suggestion['status'];
}

interface SegmentPlan {
  segment: string;
  recommended_price: number;
  price_change_pct: number;
  projected_margin_pct: number;
  pass_through_strategy: string | null;
  staged_timeline_days: number | null;
}

interface IncreaseResponse extends AdvisoryFields {
  supplier_increase_pct: number;
  blended_pass_through_pct: number;
  recommended_summary: string;
  segment_recommendations: SegmentPlan[];
}

const TARGETS = ['20', '25', '30', '35', '40', '50'];
const SENSITIVITY_LABEL: Record<string, string> = {
  highly_elastic: 'Very price-sensitive',
  elastic: 'Price-sensitive',
  mildly_inelastic: 'Somewhat price-sensitive',
  inelastic: 'Not very price-sensitive',
  unknown: 'Not enough price changes yet',
};
const STRATEGY_LABEL: Record<string, string> = {
  absorb_fully: 'Absorb the increase',
  partial: 'Pass on part of it',
  full_pass_through: 'Pass it all on',
  staged: 'Pass it on in steps',
  surcharge: 'Add a surcharge',
};
const GROUPS = [
  { tier: 'tier_1', label: 'Key accounts' },
  { tier: 'tier_2', label: 'Regional / wholesale buyers' },
  { tier: 'tier_3', label: 'Small shops' },
  { tier: 'tier_4', label: 'One-off buyers' },
] as const;
const SENSITIVITY_CHOICES = [
  { value: '', label: "Don't know" },
  { value: '-0.3', label: 'Not price-sensitive' },
  { value: '-1.2', label: 'Somewhat price-sensitive' },
  { value: '-2', label: 'Very price-sensitive' },
];

const inputClass = 'w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-300';
const buttonClass = 'rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-700 disabled:opacity-50';

function money(value: number | undefined) {
  return value == null ? '—' : value.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
}

function pct(value: number | undefined, signed = false) {
  if (value == null) return '—';
  const text = `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
  return signed && value > 0 ? `+${text}` : text;
}

function numberList(text: string): number[] | null {
  const parts = text.split(/[\s,;]+/).filter(Boolean);
  const numbers = parts.map(Number);
  return numbers.every((n) => Number.isFinite(n) && n > 0) ? numbers : null;
}

function SuggestionsTable({ items, target }: { items: ItemRow[]; target: number }) {
  if (items.length === 0) return <p className="text-sm text-slate-500">No products were found in your connected system.</p>;
  return (
    <div className="rounded-xl border border-slate-200 bg-white overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-xs text-slate-500 text-left">
          <tr>
            <th className="px-4 py-2 font-medium">Product</th>
            <th className="px-4 py-2 font-medium">Price now</th>
            <th className="px-4 py-2 font-medium">Suggested</th>
            <th className="px-4 py-2 font-medium">Margin now (target {target}%)</th>
            <th className="px-4 py-2 font-medium">How customers react to price</th>
            <th className="px-4 py-2 font-medium">Why</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {items.map((item) => (
            <tr key={`${item.item_id ?? ''}-${item.sku ?? item.product_name}`} className="align-top">
              <td className="px-4 py-3">
                <div className="font-semibold text-slate-800">{item.product_name || item.sku}</div>
                {item.sku && <div className="text-xs text-slate-400 font-mono">{item.sku}</div>}
              </td>
              {item.status === 'insufficient_data' ? (
                <td colSpan={5} className="px-4 py-3 text-slate-500">
                  <span className="font-medium text-slate-600">Not enough data. </span>{item.reason}
                </td>
              ) : (
                <>
                  <td className="px-4 py-3 text-slate-700">{money(item.current_price)}</td>
                  <td className="px-4 py-3">
                    {item.status === 'suggested_change' ? (
                      <span className="font-semibold text-slate-900">
                        {money(item.suggested_price)} <span className="text-xs text-slate-500">({pct(item.change_pct, true)})</span>
                      </span>
                    ) : (
                      <span className="text-slate-500">Keep current price</span>
                    )}
                  </td>
                  <td className={clsx('px-4 py-3', item.below_target_margin ? 'text-amber-700 font-medium' : 'text-slate-700')}>
                    {pct(item.current_margin_pct)}{item.below_target_margin && ' · below target'}
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {SENSITIVITY_LABEL[item.price_sensitivity ?? 'unknown'] ?? item.price_sensitivity}
                    <div className="text-xs text-slate-400">{item.price_changes_observed ?? 0} past price changes seen</div>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-600 max-w-xs">{item.rationale}</td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface ChangeRow { old_price: string; new_price: string; units_before: string; units_after: string; period_days: string }
const emptyChange = (): ChangeRow => ({ old_price: '', new_price: '', units_before: '', units_after: '', period_days: '30' });

function PriceCheckForm() {
  const [form, setForm] = useState({
    product_name: '', current_price: '', unit_cost: '', annual_units: '', target_margin_pct: '30', competitor_prices: '',
  });
  const [changes, setChanges] = useState<ChangeRow[]>([emptyChange()]);
  const [result, setResult] = useState<PriceCheckResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (field: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((prev) => ({ ...prev, [field]: event.target.value }));
  const setChange = (index: number, field: keyof ChangeRow, value: string) =>
    setChanges((prev) => prev.map((row, i) => (i === index ? { ...row, [field]: value } : row)));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const numbers = ['current_price', 'unit_cost', 'annual_units', 'target_margin_pct'] as const;
    const competitors = numberList(form.competitor_prices);
    const filled = changes.filter((row) => [row.old_price, row.new_price, row.units_before, row.units_after].some((v) => v.trim() !== ''));
    const history = filled.map((row) => ({
      old_price: Number(row.old_price), new_price: Number(row.new_price),
      units_before: Number(row.units_before), units_after: Number(row.units_after), period_days: Number(row.period_days),
    }));
    if (!form.product_name.trim() || numbers.some((f) => form[f].trim() === '' || !Number.isFinite(Number(form[f])))
        || !competitors || history.some((row) => Object.values(row).some((n) => !Number.isFinite(n)))) {
      setError('Fill in the product, price, cost and yearly units; complete or clear each price-change row.');
      return;
    }
    setBusy(true);
    setError(null);
    api.post<PriceCheckResponse>('/api/insights/pricing-suggestions/preview', {
      product_name: form.product_name.trim(),
      current_price: Number(form.current_price),
      unit_cost: Number(form.unit_cost),
      annual_units: Number(form.annual_units),
      target_margin_pct: Number(form.target_margin_pct),
      competitor_prices: competitors,
      price_changes: history,
    })
      .then(setResult)
      .catch((err: unknown) => {
        setResult(null);
        setError(err instanceof Error ? err.message : 'The price check failed.');
      })
      .finally(() => setBusy(false));
  };

  return (
    <section aria-label="Check a price" className="rounded-xl border border-slate-200 bg-white p-4 space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-slate-800">Check a price</h2>
        <p className="text-xs text-slate-500 mt-1">
          Enter a product's numbers and any past price changes you remember. Without at least three past changes the
          suggestion is to hold the price. Nothing is saved and no price is changed.
        </p>
      </div>
      <form onSubmit={submit} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="text-xs text-slate-600 space-y-1 sm:col-span-3">
            <span>Product</span>
            <input name="product_name" className={inputClass} value={form.product_name} onChange={set('product_name')} />
          </label>
          <label className="text-xs text-slate-600 space-y-1">
            <span>Selling price now ($)</span>
            <input name="current_price" inputMode="decimal" className={inputClass} value={form.current_price} onChange={set('current_price')} />
          </label>
          <label className="text-xs text-slate-600 space-y-1">
            <span>Your cost per unit ($)</span>
            <input name="unit_cost" inputMode="decimal" className={inputClass} value={form.unit_cost} onChange={set('unit_cost')} />
          </label>
          <label className="text-xs text-slate-600 space-y-1">
            <span>Units sold per year</span>
            <input name="annual_units" inputMode="decimal" className={inputClass} value={form.annual_units} onChange={set('annual_units')} />
          </label>
          <label className="text-xs text-slate-600 space-y-1">
            <span>Target margin (%)</span>
            <input name="target_margin_pct" inputMode="decimal" className={inputClass} value={form.target_margin_pct} onChange={set('target_margin_pct')} />
          </label>
          <label className="text-xs text-slate-600 space-y-1 sm:col-span-2">
            <span>Competitor prices you know of (optional, e.g. 9.50, 10.25)</span>
            <input name="competitor_prices" className={inputClass} value={form.competitor_prices} onChange={set('competitor_prices')} />
          </label>
        </div>
        <fieldset className="space-y-2">
          <legend className="text-xs font-semibold text-slate-600">Past price changes (optional)</legend>
          {changes.map((row, index) => (
            <div key={index} className="grid gap-2 grid-cols-2 sm:grid-cols-5">
              <input aria-label="Old price" placeholder="Old price" name={`change-${index}-old`} inputMode="decimal" className={inputClass}
                value={row.old_price} onChange={(e) => setChange(index, 'old_price', e.target.value)} />
              <input aria-label="New price" placeholder="New price" name={`change-${index}-new`} inputMode="decimal" className={inputClass}
                value={row.new_price} onChange={(e) => setChange(index, 'new_price', e.target.value)} />
              <input aria-label="Units sold before" placeholder="Units before" name={`change-${index}-before`} inputMode="decimal" className={inputClass}
                value={row.units_before} onChange={(e) => setChange(index, 'units_before', e.target.value)} />
              <input aria-label="Units sold after" placeholder="Units after" name={`change-${index}-after`} inputMode="decimal" className={inputClass}
                value={row.units_after} onChange={(e) => setChange(index, 'units_after', e.target.value)} />
              <input aria-label="Days counted before and after" placeholder="Days" name={`change-${index}-days`} inputMode="numeric" className={inputClass}
                value={row.period_days} onChange={(e) => setChange(index, 'period_days', e.target.value)} />
            </div>
          ))}
          {changes.length < 10 && (
            <button type="button" className="text-xs font-semibold text-slate-700 underline"
              onClick={() => setChanges((prev) => [...prev, emptyChange()])}>
              Add a price change
            </button>
          )}
        </fieldset>
        <button type="submit" disabled={busy} className={buttonClass}>{busy ? 'Checking…' : 'Check price'}</button>
      </form>
      {error && <ErrorState message={error} />}
      {result && (
        <div className="space-y-2" data-testid="price-check-result">
          <AdvisoryNotice source={result.source} />
          <p className="text-sm text-slate-800">
            {result.outcome === 'suggested_change'
              ? `Suggested price ${money(result.suggested_price)} (${pct(result.change_pct, true)}), margin ${pct(result.current_margin_pct)} → ${pct(result.projected_margin_pct)}, expected volume change ${pct(result.volume_impact_pct, true)}.`
              : `Keep the price at ${money(result.current_price)} for now (margin ${pct(result.current_margin_pct)}).`}
          </p>
          <p className="text-xs text-slate-500">{result.rationale}</p>
        </div>
      )}
    </section>
  );
}

function SupplierIncreaseForm() {
  const [form, setForm] = useState({ supplier: '', product_name: '', cost_before: '', cost_after: '', current_price: '' });
  const [groups, setGroups] = useState<Record<string, { units: string; ped: string }>>(
    Object.fromEntries(GROUPS.map((g) => [g.tier, { units: '', ped: '' }])),
  );
  const [result, setResult] = useState<IncreaseResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (field: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((prev) => ({ ...prev, [field]: event.target.value }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const segments = GROUPS.filter((g) => groups[g.tier].units.trim() !== '').map((g) => ({
      segment: g.label,
      pricing_tier: g.tier,
      annual_units: Number(groups[g.tier].units),
      ...(groups[g.tier].ped ? { elasticity_ped: Number(groups[g.tier].ped) } : {}),
    }));
    const prices = [form.cost_before, form.cost_after, form.current_price].map(Number);
    if (!form.supplier.trim() || !form.product_name.trim() || [form.cost_before, form.cost_after, form.current_price].some((v) => v.trim() === '')
        || prices.some((n) => !Number.isFinite(n)) || segments.length === 0 || segments.some((s) => !Number.isFinite(s.annual_units))) {
      setError('Fill in the supplier, product, costs, price and yearly units for at least one customer group.');
      return;
    }
    setBusy(true);
    setError(null);
    api.post<IncreaseResponse>('/api/insights/pricing-suggestions/supplier-increase', {
      supplier: form.supplier.trim(),
      product_name: form.product_name.trim(),
      cost_before: prices[0],
      cost_after: prices[1],
      current_price: prices[2],
      segments,
    })
      .then(setResult)
      .catch((err: unknown) => {
        setResult(null);
        setError(err instanceof Error ? err.message : 'The plan could not be worked out.');
      })
      .finally(() => setBusy(false));
  };

  return (
    <section aria-label="Supplier raised costs" className="rounded-xl border border-slate-200 bg-white p-4 space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-slate-800">A supplier raised costs: how much to pass on?</h2>
        <p className="text-xs text-slate-500 mt-1">A plan per customer group. Nothing is saved and no price is changed.</p>
      </div>
      <form onSubmit={submit} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="text-xs text-slate-600 space-y-1">
            <span>Supplier</span>
            <input name="supplier" className={inputClass} value={form.supplier} onChange={set('supplier')} />
          </label>
          <label className="text-xs text-slate-600 space-y-1 sm:col-span-2">
            <span>Product</span>
            <input name="increase_product_name" className={inputClass} value={form.product_name} onChange={set('product_name')} />
          </label>
          <label className="text-xs text-slate-600 space-y-1">
            <span>Old cost per unit ($)</span>
            <input name="cost_before" inputMode="decimal" className={inputClass} value={form.cost_before} onChange={set('cost_before')} />
          </label>
          <label className="text-xs text-slate-600 space-y-1">
            <span>New cost per unit ($)</span>
            <input name="cost_after" inputMode="decimal" className={inputClass} value={form.cost_after} onChange={set('cost_after')} />
          </label>
          <label className="text-xs text-slate-600 space-y-1">
            <span>Your selling price now ($)</span>
            <input name="increase_current_price" inputMode="decimal" className={inputClass} value={form.current_price} onChange={set('current_price')} />
          </label>
        </div>
        <fieldset className="space-y-2">
          <legend className="text-xs font-semibold text-slate-600">Customer groups (leave units blank to skip a group)</legend>
          {GROUPS.map((group) => (
            <div key={group.tier} className="grid gap-2 sm:grid-cols-3 items-center">
              <span className="text-sm text-slate-700">{group.label}</span>
              <input aria-label={`${group.label} units per year`} placeholder="Units per year" name={`${group.tier}-units`} inputMode="decimal"
                className={inputClass} value={groups[group.tier].units}
                onChange={(e) => setGroups((prev) => ({ ...prev, [group.tier]: { ...prev[group.tier], units: e.target.value } }))} />
              <select aria-label={`${group.label} price sensitivity`} name={`${group.tier}-ped`} className={inputClass}
                value={groups[group.tier].ped}
                onChange={(e) => setGroups((prev) => ({ ...prev, [group.tier]: { ...prev[group.tier], ped: e.target.value } }))}>
                {SENSITIVITY_CHOICES.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
              </select>
            </div>
          ))}
        </fieldset>
        <button type="submit" disabled={busy} className={buttonClass}>{busy ? 'Working…' : 'Plan the increase'}</button>
      </form>
      {error && <ErrorState message={error} />}
      {result && (
        <div className="space-y-2" data-testid="increase-result">
          <AdvisoryNotice source={result.source} />
          <p className="text-sm text-slate-800">
            Cost up {pct(result.supplier_increase_pct)}; pass on about {pct(result.blended_pass_through_pct)} overall.
          </p>
          <div className="rounded-xl border border-slate-200 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500 text-left">
                <tr>
                  <th className="px-4 py-2 font-medium">Customer group</th>
                  <th className="px-4 py-2 font-medium">Approach</th>
                  <th className="px-4 py-2 font-medium">New price</th>
                  <th className="px-4 py-2 font-medium">Margin after</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {result.segment_recommendations.map((plan) => (
                  <tr key={plan.segment}>
                    <td className="px-4 py-2 text-slate-700">{plan.segment}</td>
                    <td className="px-4 py-2 text-slate-700">
                      {STRATEGY_LABEL[plan.pass_through_strategy ?? ''] ?? plan.pass_through_strategy}
                      {plan.staged_timeline_days ? ` over ${plan.staged_timeline_days} days` : ''}
                    </td>
                    <td className="px-4 py-2 text-slate-800 font-medium">{money(plan.recommended_price)} ({pct(plan.price_change_pct, true)})</td>
                    <td className="px-4 py-2 text-slate-700">{pct(plan.projected_margin_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-slate-500">{result.recommended_summary}</p>
        </div>
      )}
    </section>
  );
}

export default function PricingSuggestionsInsight() {
  const [target, setTarget] = useState('30');
  const { data, error, loading, reload } = useInsight<PricingResponse>(
    `/api/insights/pricing-suggestions?target_margin_pct=${target}`,
  );

  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source={data?.source ?? 'connected_data'} />
      <label className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
        <span>Target gross margin:</span>
        <select name="target_margin" className="rounded-lg border border-slate-300 px-2 py-1 text-xs"
          value={target} onChange={(event) => setTarget(event.target.value)}>
          {TARGETS.map((value) => <option key={value} value={value}>{value}%</option>)}
        </select>
      </label>
      {loading && <LoadingState />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && !loading && (
        <section aria-label="Suggestions from your sales" className="space-y-3">
          <p className="text-sm text-slate-700">
            {data.summary.suggested_changes} price changes suggested, {data.summary.holds} to keep as they are,
            {' '}{data.summary.below_target_margin} below your target margin.
          </p>
          <p className="text-xs text-slate-500">
            Uses your QuickBooks prices and costs and the last 12 months of invoices. A change is only suggested when
            customers' reactions to at least three past price moves point the same way.
            {data.summary.rows_skipped > 0 && ` ${data.summary.rows_skipped} unreadable records were skipped.`}
          </p>
          <SuggestionsTable items={data.items} target={data.target_margin_pct} />
        </section>
      )}
      <PriceCheckForm />
      <SupplierIncreaseForm />
    </InsightShell>
  );
}
