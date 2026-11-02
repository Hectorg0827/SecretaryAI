import { useState, type FormEvent } from 'react';
import { api } from '../../../lib/api';
import type { InsightMeta } from '../registry';
import { AdvisoryNotice, ErrorState, InsightShell, type AdvisoryFields } from '../ui';

export const meta: InsightMeta = {
  slug: 'product-concepts',
  title: 'New product ideas',
  description: 'Size a product idea: untapped demand, first-year profit, payback and sales taken from your existing products.',
  group: 'Growth',
  // Backend permission view_financials admits owner and manager only.
  roles: ['owner', 'manager'],
};

interface ConceptResponse extends AdvisoryFields {
  concept_name: string;
  addressable_whitespace_cases: number;
  projected_new_product_cases: number;
  cannibalized_incumbent_cases: number;
  gross_new_product_contribution: number;
  incumbent_contribution_lost: number;
  incremental_first_year_profit: number;
  estimated_payback_months: number | null;
  documented_assumptions: boolean;
  risk_flags: string[];
}

const FIELDS = [
  { key: 'observed_category_demand_cases', label: 'Total yearly demand in this category (cases)' },
  { key: 'existing_served_category_cases', label: 'Cases of that demand you already sell' },
  { key: 'proposed_capture_pct', label: 'Share of the untapped demand you expect to win (%)' },
  { key: 'wholesale_price_per_case', label: 'Wholesale price per case' },
  { key: 'landed_cost_per_case', label: 'Landed cost per case' },
  { key: 'variable_launch_cost_per_case', label: 'Extra launch cost per case' },
  { key: 'one_time_launch_cost', label: 'One-time launch cost' },
  { key: 'cannibalization_pct', label: 'Share of new sales taken from your current products (%)' },
  { key: 'incumbent_contribution_per_case', label: 'Profit per case on the current products it replaces' },
] as const;

const FLAGS: Record<string, string> = {
  unverified_market_or_assumptions: 'The demand figure is not tied to a dated source, so treat it as a rough guess.',
  nonpositive_new_product_unit_contribution: 'Each case would not make money at this price and cost.',
  first_year_profit_not_positive: 'The idea does not pay back its launch cost in the first year.',
  cannibalization_requires_portfolio_review: 'It takes sales from your current products; review the range before launching.',
};

const initialValues = (): Record<string, string> => ({
  concept_name: '', demand_source_reference: '', assumption_version: '',
  ...Object.fromEntries(FIELDS.map(({ key }) => [key, ''])),
});

const cases = (value: number) => `${value.toLocaleString()} cases`;
const money = (value: number) => value.toLocaleString(undefined, { style: 'currency', currency: 'USD' });

export default function ProductConcepts() {
  const [values, setValues] = useState<Record<string, string>>(initialValues);
  const [result, setResult] = useState<ConceptResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const set = (key: string, value: string) => setValues((current) => ({ ...current, [key]: value }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!values.concept_name.trim()) {
      setError('Give the product idea a name.');
      return;
    }
    const body: Record<string, unknown> = {
      concept_name: values.concept_name.trim(),
      demand_source_reference: values.demand_source_reference.trim(),
      assumption_version: values.assumption_version.trim(),
    };
    for (const { key } of FIELDS) {
      const value = Number(values[key]);
      if (values[key].trim() === '' || !Number.isFinite(value)) {
        setError('Fill in every number; use 0 where something does not apply.');
        return;
      }
      body[key] = value;
    }
    setError(null);
    setSubmitting(true);
    api.post<ConceptResponse>('/api/insights/product-concepts/preview', body)
      .then((response) => setResult(response))
      .catch((err: unknown) => {
        setResult(null);
        setError(err instanceof Error ? err.message : 'Could not assess this idea.');
      })
      .finally(() => setSubmitting(false));
  };

  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source="user_supplied" />
      <p className="text-xs text-slate-500">
        Market size and launch assumptions are not in your connected systems, so enter your own estimates. Nothing is saved.
      </p>
      <form onSubmit={submit} className="space-y-4 rounded-xl border border-slate-200 bg-white p-4">
        <label className="block text-xs text-slate-600">Product idea
          <input name="concept_name" value={values.concept_name} maxLength={120}
            onChange={(e) => set('concept_name', e.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
        </label>
        <div className="grid gap-2 sm:grid-cols-3">
          {FIELDS.map(({ key, label }) => (
            <label key={key} className="text-xs text-slate-600">{label}
              <input name={key} type="number" min="0" step="any" value={values[key]}
                onChange={(e) => set(key, e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
            </label>
          ))}
          <label className="text-xs text-slate-600">Where the demand figure comes from (optional)
            <input name="demand_source_reference" value={values.demand_source_reference} maxLength={120}
              placeholder="e.g. Category study" onChange={(e) => set('demand_source_reference', e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
          </label>
          <label className="text-xs text-slate-600">Date or version of those assumptions (optional)
            <input name="assumption_version" value={values.assumption_version} maxLength={60}
              placeholder="e.g. September 2026" onChange={(e) => set('assumption_version', e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
          </label>
        </div>
        <button type="submit" disabled={submitting}
          className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-700 disabled:opacity-50">
          {submitting ? 'Assessing…' : 'Assess idea'}
        </button>
      </form>
      {error && <ErrorState message={error} />}
      {result && (
        <section aria-label="Assessment" className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { label: 'Untapped demand', value: cases(result.addressable_whitespace_cases) },
              { label: 'Expected first-year sales', value: cases(result.projected_new_product_cases) },
              { label: 'First-year profit after launch cost', value: money(result.incremental_first_year_profit) },
              {
                label: 'Months to pay back launch cost',
                value: result.estimated_payback_months == null ? 'Does not pay back' : result.estimated_payback_months.toFixed(1),
              },
            ].map(({ label, value }) => (
              <div key={label} className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="text-xs text-slate-500">{label}</div>
                <div className="text-xl font-bold text-slate-900">{value}</div>
              </div>
            ))}
          </div>
          <p className="text-xs text-slate-500">
            Profit from the new product {money(result.gross_new_product_contribution)}, minus{' '}
            {money(result.incumbent_contribution_lost)} lost on {cases(result.cannibalized_incumbent_cases)} taken from
            your current products.
          </p>
          {result.risk_flags.length > 0 && (
            <ul className="list-disc pl-5 text-sm text-amber-800 space-y-0.5">
              {result.risk_flags.map((flag) => <li key={flag}>{FLAGS[flag] ?? flag.replace(/_/g, ' ')}</li>)}
            </ul>
          )}
        </section>
      )}
    </InsightShell>
  );
}
