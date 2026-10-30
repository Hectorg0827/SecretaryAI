import { useState, type FormEvent } from 'react';
import { api } from '../../../lib/api';
import type { InsightMeta } from '../registry';
import { AdvisoryNotice, ErrorState, InsightShell, type AdvisoryFields } from '../ui';

// Backend: POST /api/insights/supplier-tco/preview requires view_financials.
export const meta: InsightMeta = {
  slug: 'supplier-tco',
  title: 'Supplier true cost comparison',
  description: 'Compare suppliers on the all-in cost per unit (freight, duty, returns, storage) and sustainability.',
  group: 'Supply chain',
  roles: ['owner', 'manager'],
};

const COST_FIELDS = [
  { name: 'purchase_price', label: 'Price per unit ($)' },
  { name: 'inbound_freight_per_unit', label: 'Freight to you, per unit ($)' },
  { name: 'customs_duty_rate_pct', label: 'Import duty (% of price)' },
  { name: 'brokerage_per_unit', label: 'Customs broker fee, per unit ($)' },
  { name: 'days_inventory_outstanding', label: 'Days stock sits before it sells' },
  { name: 'holding_cost_rate_pct', label: 'Storage cost (% of value per year)' },
  { name: 'return_rate_pct', label: 'Units returned or defective (%)' },
  { name: 'return_handling_cost', label: 'Cost to handle one return ($)' },
  { name: 'replacement_cost_per_return', label: 'Cost to replace one return ($)' },
  { name: 'kg_co2e_per_unit', label: 'Emissions per unit (kg CO₂e)' },
  { name: 'volume_rebate_pct', label: 'Volume rebate (%)' },
] as const;
type CostField = typeof COST_FIELDS[number]['name'];

const ESG_FIELDS = [
  { name: 'carbon_per_case', label: 'Carbon per case (kg CO₂e)' },
  { name: 'packaging_waste_kg_per_unit', label: 'Packaging waste per unit (kg)' },
  { name: 'recyclable_packaging_pct', label: 'Recyclable packaging (%)' },
  { name: 'water_intensity', label: 'Water use (gallons per $1k sales)' },
  { name: 'safety_incidents', label: 'Safety incidents per 100 staff' },
  { name: 'supplier_audit_pass_rate', label: 'Ethical audit pass rate (%)' },
  { name: 'local_sourcing_pct', label: 'Locally sourced (%)' },
  { name: 'on_time_payment_rate', label: 'Pays its own suppliers on time (%)' },
  { name: 'import_compliance_rate', label: 'Import paperwork accuracy (%)' },
] as const;
type EsgField = typeof ESG_FIELDS[number]['name'];

interface SupplierForm {
  name: string;
  costs: Record<CostField, string>;
  withEsg: boolean;
  esg: Record<EsgField, string>;
}

interface Breakdown {
  supplier_id: string;
  cost_per_unit: Record<string, number>;
  total_annual_tco: number;
  non_purchase_cost_pct: number;
}

interface TcoResponse extends AdvisoryFields {
  recommended_supplier_id: string;
  annual_savings_vs_current: number;
  rationale: string;
  supplier_comparison: Breakdown[];
  esg_scores: { entity_id: string; scores: Record<string, number>; improvement_opportunities: string[] }[];
}

const newSupplier = (): SupplierForm => ({
  name: '',
  costs: Object.fromEntries(COST_FIELDS.map(({ name }) => [
    name, name === 'holding_cost_rate_pct' ? '25' : name === 'volume_rebate_pct' ? '0' : '',
  ])) as Record<CostField, string>,
  withEsg: false,
  esg: Object.fromEntries(ESG_FIELDS.map(({ name }) => [name, ''])) as Record<EsgField, string>,
});

const inputClass = 'w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-300';

function money(value: number, digits = 2) {
  return value.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: digits });
}

function numbers<K extends string>(values: Record<K, string>): Record<K, number> | null {
  const out = {} as Record<K, number>;
  for (const [key, raw] of Object.entries(values) as [K, string][]) {
    const value = Number(raw);
    if (raw.trim() === '' || !Number.isFinite(value) || value < 0) return null;
    out[key] = value;
  }
  return out;
}

function Results({ result }: { result: TcoResponse }) {
  return (
    <div className="space-y-3" data-testid="tco-result">
      <AdvisoryNotice source={result.source} />
      <p className="text-sm text-slate-800">
        Lowest all-in cost: <span className="font-semibold">{result.recommended_supplier_id}</span>
        {result.annual_savings_vs_current > 0 && ` — about ${money(result.annual_savings_vs_current, 0)} a year less than your current supplier`}.
      </p>
      <p className="text-xs text-slate-500">{result.rationale}</p>
      <div className="rounded-xl border border-slate-200 bg-white overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500 text-left">
            <tr>
              <th className="px-4 py-2 font-medium">Supplier (cheapest first)</th>
              <th className="px-4 py-2 font-medium">All-in cost per unit</th>
              <th className="px-4 py-2 font-medium">Invoice price</th>
              <th className="px-4 py-2 font-medium">Costs beyond the invoice</th>
              <th className="px-4 py-2 font-medium">Per year</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {result.supplier_comparison.map((row) => (
              <tr key={row.supplier_id}>
                <td className="px-4 py-2 font-semibold text-slate-800">{row.supplier_id}</td>
                <td className="px-4 py-2 text-slate-800">{money(row.cost_per_unit.total, 4)}</td>
                <td className="px-4 py-2 text-slate-600">{money(row.cost_per_unit.purchase_price, 4)}</td>
                <td className="px-4 py-2 text-slate-600">{row.non_purchase_cost_pct}%</td>
                <td className="px-4 py-2 text-slate-600">{money(row.total_annual_tco, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {result.esg_scores.length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-white p-4 space-y-2">
          <div className="text-xs font-semibold text-slate-600">Sustainability scores (0–100, rough reference values)</div>
          {result.esg_scores.map((esg) => (
            <div key={esg.entity_id} className="text-sm text-slate-700">
              <span className="font-semibold">{esg.entity_id}:</span> overall {esg.scores.overall}, environment {esg.scores.environmental},
              {' '}people {esg.scores.social}, governance {esg.scores.governance}
              {esg.improvement_opportunities.length > 0 && (
                <ul className="list-disc pl-5 text-xs text-slate-500">
                  {esg.improvement_opportunities.map((item) => <li key={item}>{item}</li>)}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function SupplierTcoInsight() {
  const [product, setProduct] = useState('');
  const [volume, setVolume] = useState('');
  const [unit, setUnit] = useState('case');
  const [wacc, setWacc] = useState('10');
  const [suppliers, setSuppliers] = useState<SupplierForm[]>([newSupplier(), newSupplier()]);
  const [current, setCurrent] = useState(0);
  const [result, setResult] = useState<TcoResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const update = (index: number, change: (s: SupplierForm) => SupplierForm) =>
    setSuppliers((prev) => prev.map((s, i) => (i === index ? change(s) : s)));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const rows = suppliers.map((s) => ({ s, costs: numbers(s.costs), esg: s.withEsg ? numbers(s.esg) : undefined }));
    if (!product.trim() || !unit.trim() || !(Number(volume) > 0) || !Number.isFinite(Number(wacc))
        || rows.some((r) => !r.s.name.trim() || !r.costs || r.esg === null)) {
      setError('Fill in the product, yearly volume and every cost for each supplier (use 0 where a cost does not apply).');
      return;
    }
    setBusy(true);
    setError(null);
    api.post<TcoResponse>('/api/insights/supplier-tco/preview', {
      product: product.trim(),
      annual_volume: Number(volume),
      unit_of_measure: unit.trim(),
      currency: 'USD',
      wacc_pct: Number(wacc),
      current_supplier: suppliers[current].name.trim(),
      suppliers: rows.map((r) => ({ name: r.s.name.trim(), ...r.costs, ...(r.esg ? { esg: r.esg } : {}) })),
    })
      .then(setResult)
      .catch((err: unknown) => {
        setResult(null);
        setError(err instanceof Error ? err.message : 'The comparison failed.');
      })
      .finally(() => setBusy(false));
  };

  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source="user_supplied" />
      <p className="text-xs text-slate-500">
        Freight, duty, returns and emissions aren't held in your connected systems, so enter them here.
        Every supplier is compared at the same yearly volume. Nothing is saved and no supplier is contacted.
      </p>
      <form onSubmit={submit} className="space-y-4" aria-label="Supplier comparison">
        <div className="rounded-xl border border-slate-200 bg-white p-4 grid gap-3 sm:grid-cols-4">
          <label className="text-xs text-slate-600 space-y-1 sm:col-span-2">
            <span>Product</span>
            <input name="product" className={inputClass} value={product} onChange={(e) => setProduct(e.target.value)} />
          </label>
          <label className="text-xs text-slate-600 space-y-1">
            <span>Units bought per year</span>
            <input name="annual_volume" inputMode="decimal" className={inputClass} value={volume} onChange={(e) => setVolume(e.target.value)} />
          </label>
          <label className="text-xs text-slate-600 space-y-1">
            <span>Unit (e.g. case)</span>
            <input name="unit_of_measure" className={inputClass} value={unit} onChange={(e) => setUnit(e.target.value)} />
          </label>
          <label className="text-xs text-slate-600 space-y-1">
            <span>Cost of money tied up in stock (% per year)</span>
            <input name="wacc_pct" inputMode="decimal" className={inputClass} value={wacc} onChange={(e) => setWacc(e.target.value)} />
          </label>
        </div>
        <div className="grid gap-3 lg:grid-cols-2">
          {suppliers.map((supplier, index) => (
            <fieldset key={index} className="rounded-xl border border-slate-200 bg-white p-4 space-y-3" data-testid="supplier-card">
              <legend className="sr-only">Supplier {index + 1}</legend>
              <div className="flex flex-wrap items-end gap-3">
                <label className="text-xs text-slate-600 space-y-1 flex-1">
                  <span>Supplier name</span>
                  <input name={`supplier-${index}-name`} className={inputClass} value={supplier.name}
                    onChange={(e) => update(index, (s) => ({ ...s, name: e.target.value }))} />
                </label>
                <label className="flex items-center gap-1 text-xs text-slate-600 pb-2">
                  <input type="radio" name="current_supplier" checked={current === index} onChange={() => setCurrent(index)} />
                  Current supplier
                </label>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {COST_FIELDS.map((field) => (
                  <label key={field.name} className="text-xs text-slate-600 space-y-1">
                    <span>{field.label}</span>
                    <input name={`supplier-${index}-${field.name}`} inputMode="decimal" className={inputClass}
                      value={supplier.costs[field.name]}
                      onChange={(e) => update(index, (s) => ({ ...s, costs: { ...s.costs, [field.name]: e.target.value } }))} />
                  </label>
                ))}
              </div>
              <label className="flex items-center gap-2 text-xs text-slate-600">
                <input type="checkbox" name={`supplier-${index}-with-esg`} checked={supplier.withEsg}
                  onChange={(e) => update(index, (s) => ({ ...s, withEsg: e.target.checked }))} />
                Add sustainability scores
              </label>
              {supplier.withEsg && (
                <div className="grid gap-2 sm:grid-cols-2">
                  {ESG_FIELDS.map((field) => (
                    <label key={field.name} className="text-xs text-slate-600 space-y-1">
                      <span>{field.label}</span>
                      <input name={`supplier-${index}-esg-${field.name}`} inputMode="decimal" className={inputClass}
                        value={supplier.esg[field.name]}
                        onChange={(e) => update(index, (s) => ({ ...s, esg: { ...s.esg, [field.name]: e.target.value } }))} />
                    </label>
                  ))}
                </div>
              )}
            </fieldset>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={busy} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-700 disabled:opacity-50">
            {busy ? 'Comparing…' : 'Compare suppliers'}
          </button>
          {suppliers.length < 6 && (
            <button type="button" className="text-xs font-semibold text-slate-700 underline"
              onClick={() => setSuppliers((prev) => [...prev, newSupplier()])}>
              Add a supplier
            </button>
          )}
          {suppliers.length > 2 && (
            <button type="button" className="text-xs text-slate-500 underline"
              onClick={() => {
                setSuppliers((prev) => prev.slice(0, -1));
                setCurrent((prev) => Math.min(prev, suppliers.length - 2));
              }}>
              Remove last supplier
            </button>
          )}
        </div>
      </form>
      {error && <ErrorState message={error} />}
      {result && <Results result={result} />}
    </InsightShell>
  );
}
