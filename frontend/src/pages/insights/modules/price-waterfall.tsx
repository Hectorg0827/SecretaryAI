import { useState, type FormEvent } from 'react';
import { api } from '../../../lib/api';
import type { InsightMeta } from '../registry';
import { AdvisoryNotice, ErrorState, InsightShell, type AdvisoryFields } from '../ui';

export const meta: InsightMeta = {
  slug: 'price-waterfall',
  title: 'Price waterfall',
  description: 'Follow one case from list price down to the pocket price and margin you actually keep.',
  group: 'Pricing',
  // Backend permission view_financials admits owner and manager only.
  roles: ['owner', 'manager'],
};

interface Step { key: string; label: string; amount: number; running: number }

interface WaterfallResponse extends AdvisoryFields {
  product_label: string;
  currency: string;
  steps: Step[];
  pocket_price_per_case: number;
  landed_cost_per_case: number;
  gross_margin_pct: number;
  contribution_per_case: number;
  contribution_margin_pct: number;
  estimated_distributor_to_retailer: number;
  estimated_retailer_to_consumer: number;
  missing_cost_components: string[];
  exception_reasons: string[];
}

const PRICE_FIELDS = [
  { key: 'price_per_case', label: 'List price per case', required: true },
  { key: 'approved_price_per_case', label: 'Approved price to compare against (optional)' },
  { key: 'discount_per_case', label: 'Discount per case' },
  { key: 'returns_allowance_per_case', label: 'Returns allowance per case' },
  { key: 'commission_per_case', label: 'Sales commission per case' },
  { key: 'marketing_per_case', label: 'Marketing support per case' },
] as const;

const COST_FIELDS = [
  { key: 'product', label: 'Product cost' },
  { key: 'international_freight', label: 'International freight' },
  { key: 'insurance', label: 'Insurance' },
  { key: 'duty', label: 'Duty' },
  { key: 'customs_brokerage', label: 'Customs brokerage' },
  { key: 'port_fees', label: 'Port fees' },
  { key: 'domestic_freight', label: 'Domestic freight' },
  { key: 'excise', label: 'Excise tax' },
  { key: 'other_taxes', label: 'Other taxes' },
] as const;

const PCT_FIELDS = [
  { key: 'target_margin_pct', label: 'Your target gross margin %', initial: '30' },
  { key: 'distributor_margin_pct', label: 'Distributor margin %', initial: '25' },
  { key: 'retailer_margin_pct', label: 'Retailer margin %', initial: '30' },
] as const;

const COST_LABELS: Record<string, string> = Object.fromEntries(COST_FIELDS.map(({ key, label }) => [key, label]));

const REASONS: Record<string, string> = {
  missing_cost_categories: 'Some landed-cost items were left blank, so the cost may be understated.',
  unreconciled_cost_source_revision: 'The costs are not tied to a dated source record.',
  below_target_margin: 'Gross margin is below your target.',
  price_exception_requires_approval: 'The list price differs from your approved price and needs approval.',
  missing_approved_price_reference: 'No approved price was given to compare against.',
  negative_unit_contribution: 'Each case loses money after all costs.',
};

const initialValues = (): Record<string, string> => ({
  product_label: '', cost_evidence_reference: '', cost_evidence_version: '',
  ...Object.fromEntries(PRICE_FIELDS.map(({ key }) => [key, ''])),
  ...Object.fromEntries(COST_FIELDS.map(({ key }) => [key, ''])),
  ...Object.fromEntries(PCT_FIELDS.map(({ key, initial }) => [key, initial])),
});

function NumberInput({ name, label, value, onChange }: {
  name: string; label: string; value: string; onChange: (value: string) => void;
}) {
  return (
    <label className="text-xs text-slate-600">{label}
      <input name={name} type="number" min="0" step="any" value={value} onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
    </label>
  );
}

export default function PriceWaterfall() {
  const [values, setValues] = useState<Record<string, string>>(initialValues);
  const [result, setResult] = useState<WaterfallResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const set = (key: string) => (value: string) => setValues((current) => ({ ...current, [key]: value }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const parse = (key: string) => (values[key].trim() === '' ? null : Number(values[key]));
    const body: Record<string, unknown> = {
      product_label: values.product_label.trim(),
      cost_evidence_reference: values.cost_evidence_reference.trim(),
      cost_evidence_version: values.cost_evidence_version.trim(),
    };
    const numbers = [...PRICE_FIELDS, ...PCT_FIELDS].map(({ key }) => [key, parse(key)] as const);
    if (numbers.some(([, value]) => value != null && !Number.isFinite(value))) {
      setError('Use plain numbers only.');
      return;
    }
    if (parse('price_per_case') == null) {
      setError('Enter the list price per case.');
      return;
    }
    for (const [key, value] of numbers) if (value != null) body[key] = value;
    body.cost_lines = COST_FIELDS
      .filter(({ key }) => parse(key) != null)
      .map(({ key }) => ({ component: key, amount_per_case: parse(key) }));
    setError(null);
    setSubmitting(true);
    api.post<WaterfallResponse>('/api/insights/price-waterfall/preview', body)
      .then((response) => setResult(response))
      .catch((err: unknown) => {
        setResult(null);
        setError(err instanceof Error ? err.message : 'Could not calculate this waterfall.');
      })
      .finally(() => setSubmitting(false));
  };

  const fmt = (value: number) => value.toLocaleString(undefined, {
    style: 'currency', currency: result?.currency ?? 'USD',
  });
  const top = result ? Math.max(...result.steps.map((step) => Math.abs(step.running)), 1) : 1;

  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source="user_supplied" />
      <p className="text-xs text-slate-500">
        Discounts, allowances and landed costs are not in your connected systems, so enter them per case. Nothing is saved.
      </p>
      <form onSubmit={submit} className="space-y-4 rounded-xl border border-slate-200 bg-white p-4">
        <label className="block text-xs text-slate-600">Product (optional)
          <input name="product_label" value={values.product_label} maxLength={120}
            onChange={(e) => set('product_label')(e.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
        </label>
        <fieldset className="grid gap-2 sm:grid-cols-3">
          <legend className="text-xs font-semibold text-slate-500 mb-1">Price and deductions</legend>
          {PRICE_FIELDS.map(({ key, label }) => (
            <NumberInput key={key} name={key} label={label} value={values[key]} onChange={set(key)} />
          ))}
        </fieldset>
        <fieldset className="grid gap-2 sm:grid-cols-3">
          <legend className="text-xs font-semibold text-slate-500 mb-1">Landed cost per case (leave blank if unknown)</legend>
          {COST_FIELDS.map(({ key, label }) => (
            <NumberInput key={key} name={key} label={label} value={values[key]} onChange={set(key)} />
          ))}
          <label className="text-xs text-slate-600">Where these costs come from (optional)
            <input name="cost_evidence_reference" value={values.cost_evidence_reference} maxLength={120}
              placeholder="e.g. Broker invoice" onChange={(e) => set('cost_evidence_reference')(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
          </label>
          <label className="text-xs text-slate-600">Date or version of that record (optional)
            <input name="cost_evidence_version" value={values.cost_evidence_version} maxLength={60}
              placeholder="e.g. October 2026" onChange={(e) => set('cost_evidence_version')(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
          </label>
        </fieldset>
        <fieldset className="grid gap-2 sm:grid-cols-3">
          <legend className="text-xs font-semibold text-slate-500 mb-1">Margins</legend>
          {PCT_FIELDS.map(({ key, label }) => (
            <NumberInput key={key} name={key} label={label} value={values[key]} onChange={set(key)} />
          ))}
        </fieldset>
        <button type="submit" disabled={submitting}
          className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-700 disabled:opacity-50">
          {submitting ? 'Calculating…' : 'Show waterfall'}
        </button>
      </form>
      {error && <ErrorState message={error} />}
      {result && (
        <section aria-label="Waterfall result" className="space-y-3">
          <div className="rounded-xl border border-slate-200 bg-white p-4 space-y-2">
            {result.steps.map((step) => (
              <div key={step.key} className="grid grid-cols-[10rem_1fr_6rem_6rem] items-center gap-2 text-sm">
                <span className="text-slate-600">{step.label}</span>
                <div className="h-3 rounded bg-slate-100">
                  <div className={`h-3 rounded ${step.running < 0 ? 'bg-red-400' : 'bg-slate-500'}`}
                    style={{ width: `${(Math.abs(step.running) / top) * 100}%` }} />
                </div>
                <span className="text-right text-xs text-slate-500">{step.key === 'list_price' ? '' : fmt(step.amount)}</span>
                <span className="text-right font-semibold text-slate-800">{fmt(step.running)}</span>
              </div>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="text-xs text-slate-500">Pocket price per case</div>
              <div className="text-xl font-bold text-slate-900">{fmt(result.pocket_price_per_case)}</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="text-xs text-slate-500">Gross margin</div>
              <div className="text-xl font-bold text-slate-900">{result.gross_margin_pct.toFixed(1)}%</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="text-xs text-slate-500">Profit per case after all costs</div>
              <div className="text-xl font-bold text-slate-900">{fmt(result.contribution_per_case)}</div>
              <div className="text-xs text-slate-500">{result.contribution_margin_pct.toFixed(1)}% of net revenue</div>
            </div>
          </div>
          <p className="text-xs text-slate-500">
            Estimated shelf prices at these margins: {fmt(result.estimated_distributor_to_retailer)} to the retailer,{' '}
            {fmt(result.estimated_retailer_to_consumer)} to the consumer (before retail taxes).
          </p>
          {result.exception_reasons.length > 0 && (
            <ul className="list-disc pl-5 text-sm text-amber-800 space-y-0.5">
              {result.exception_reasons.map((reason) => <li key={reason}>{REASONS[reason] ?? reason.replace(/_/g, ' ')}</li>)}
            </ul>
          )}
          {result.missing_cost_components.length > 0 && (
            <p className="text-xs text-slate-500">
              Not entered: {result.missing_cost_components.map((key) => COST_LABELS[key] ?? key).join(', ')}.
            </p>
          )}
        </section>
      )}
    </InsightShell>
  );
}
