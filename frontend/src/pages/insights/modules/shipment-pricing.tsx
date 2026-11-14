import { useState, type FormEvent } from 'react';
import { api } from '../../../lib/api';
import type { InsightMeta } from '../registry';
import { AdvisoryNotice, ErrorState, InsightShell } from '../ui';

export const meta: InsightMeta = {
  slug: 'shipment-pricing',
  title: 'Shipment cost allocation',
  description: 'Allocate shared import and warehouse expenses across saleable cases, with invoice-cent reconciliation.',
  group: 'Pricing',
  roles: ['owner', 'manager'],
};

type Product = {
  sku: string; accepted_cases: string; product_cost_per_case: string;
  weight_kg: string; volume_cbm: string; pallet_days: string; picks: string;
  discount_per_case: string; returns_allowance_per_case: string;
  commission_per_case: string; marketing_per_case: string;
};
type Expense = {
  expense_line_ref: string; component: string; amount: string;
  currency: string; allocation_basis: string; evidence_ref: string;
  evidence_version: string;
};
type ResultLine = {
  sku: string; accepted_cases: string;
  inbound_cost_per_case: string; warehouse_cost_per_case: string;
  fully_loaded_cost_per_case: string;
  minimum_list_price_at_target_contribution: string;
};
type Result = {
  status: 'analysis_only';
  products: ResultLine[];
  expenses_total: string;
  charges: Array<{ expense_line_ref: string; invoice_amount: string; allocated: Record<string, string>; rounding_residual: string }>;
  missing_inbound_components: string[];
  missing_warehouse_components: string[];
  costs_reconciled: false;
  prices_updated: false;
};
type FinanceAssessment = {
  assessment_id: string; po_number: string; assertion_status: string;
  landed_cost_total: string; currency: string;
  lines: Array<{
    sku: string; unit_cost: string; allocated_landed_cost: string;
    cases: { available: string; damaged: string; quarantined: string };
  }>;
  valuation_verified: boolean;
  accounting_reconciled: boolean;
};

const COSTS = [
  { value: 'international_freight', label: 'International freight' },
  { value: 'insurance', label: 'Cargo insurance' },
  { value: 'duty', label: 'Duty and tariffs' },
  { value: 'customs_brokerage', label: 'Customs brokerage' },
  { value: 'port_fees', label: 'Port and terminal charges' },
  { value: 'domestic_freight', label: 'Port to warehouse freight / drayage' },
  { value: 'excise', label: 'Excise taxes' },
  { value: 'other_taxes', label: 'Other import taxes' },
  { value: 'warehouse_receiving', label: 'Warehouse receiving' },
  { value: 'warehouse_storage', label: 'Warehouse storage (pallet-days)' },
  { value: 'warehouse_handling', label: 'Pick and handling' },
  { value: 'warehouse_outbound', label: 'Outbound warehouse delivery' },
  { value: 'warehouse_shrink', label: 'Damage and shrink allowance' },
  { value: 'inventory_carrying', label: 'Inventory financing' },
] as const;

const BASES = [
  { value: 'cases', label: 'Saleable cases' },
  { value: 'weight_kg', label: 'Weight (kg)' },
  { value: 'volume_cbm', label: 'Volume (m³)' },
  { value: 'pallet_days', label: 'Pallet-days' },
  { value: 'picks', label: 'Order picks' },
] as const;

const STYLE = 'w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800';
const BUTTON = 'rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-40';

function blankProduct(): Product {
  return {
    sku: '', accepted_cases: '', product_cost_per_case: '',
    weight_kg: '0', volume_cbm: '0', pallet_days: '0', picks: '0',
    discount_per_case: '0', returns_allowance_per_case: '0',
    commission_per_case: '0', marketing_per_case: '0',
  };
}
function blankExpense(i: number): Expense {
  return {
    expense_line_ref: `expense-${i}`, component: 'warehouse_storage', amount: '',
    currency: 'USD', allocation_basis: 'pallet_days', evidence_ref: '', evidence_version: '',
  };
}

export default function ShipmentPricing() {
  const [shipment, setShipment] = useState('');
  const [target, setTarget] = useState('30');
  const [products, setProducts] = useState<Product[]>([blankProduct()]);
  const [expenses, setExpenses] = useState<Expense[]>([blankExpense(1)]);
  const [assessmentId, setAssessmentId] = useState('');
  const [assessment, setAssessment] = useState<FinanceAssessment | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingSource, setLoadingSource] = useState(false);
  const [counter, setCounter] = useState(2);

  const updateProduct = (i: number, key: keyof Product, value: string) =>
    setProducts((rows) => rows.map((r, idx) => idx === i ? { ...r, [key]: value } : r));
  const updateExpense = (i: number, key: keyof Expense, value: string) =>
    setExpenses((rows) => rows.map((r, idx) => idx === i ? { ...r, [key]: value } : r));

  const loadAssessment = async () => {
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(assessmentId.trim())) {
      setError('Enter a valid purchasing assessment UUID.');
      return;
    }
    setError(null); setLoadingSource(true); setResult(null);
    try {
      const source = await api.get<FinanceAssessment>(
        `/api/logistics/finance/assessments/${encodeURIComponent(assessmentId.trim())}`,
      );
      if (source.currency !== 'USD') {
        throw new Error('The assessment currency is not USD. Verify and document the FX conversion before using these supplier costs for USD pricing.');
      }
      if (!source.lines?.length) throw new Error('The assessment has no usable product lines.');
      const mapped = source.lines
        .filter((line) => Number(line.cases?.available) > 0)
        .map((line): Product => ({
          ...blankProduct(),
          sku: line.sku, accepted_cases: line.cases.available,
          product_cost_per_case: line.unit_cost,
        }));
      if (!mapped.length) throw new Error('There are no independently accepted saleable cases.');
      setProducts(mapped); setShipment(source.po_number);
      setAssessment(source);
    } catch (err) {
      setAssessment(null);
      setError(err instanceof Error ? err.message : 'Purchasing source unavailable.');
    } finally { setLoadingSource(false); }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!shipment.trim() || products.some((p) => !p.sku.trim() || !p.accepted_cases || !p.product_cost_per_case)) {
      setError('Enter a shipment reference and the saleable cases and supplier cost for each SKU.');
      return;
    }
    if (expenses.some((x) => !x.amount.trim() || !x.evidence_ref.trim() || !x.evidence_version.trim())) {
      setError('Every shared charge needs an amount, invoice reference, and revision. Use 0.00 for evidenced no-charge items.');
      return;
    }
    setError(null); setBusy(true); setResult(null);
    try {
      const response = await api.post<Result>('/api/insights/shipment-pricing/preview', {
        shipment_ref: shipment.trim(), currency: 'USD',
        target_contribution_margin_pct: target,
        products, expenses,
      });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The allocation cannot be calculated safely.');
    } finally { setBusy(false); }
  };

  return <InsightShell title={meta.title} description={meta.description}>
    <AdvisoryNotice source="user_supplied" />
    <p className="text-sm text-slate-600">
      All numbers are provisional until finance reconciles freight and warehouse invoices,
      warehouse receipts and the approved price book. Cost allocation does not change QuickBooks.
    </p>
    <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold">Load approved physical receipts (optional)</h2>
      <p className="text-xs text-slate-500">
        Paste the UUID of a purchasing finance assessment to read SKU quantities and provisional supplier cost.
        Previously allocated inbound charges are NOT imported twice.
      </p>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input aria-label="Purchasing assessment UUID" value={assessmentId}
          onChange={(e) => setAssessmentId(e.target.value)} placeholder="Purchasing assessment UUID"
          className={STYLE} />
        <button type="button" onClick={loadAssessment} disabled={loadingSource} className={BUTTON}>
          {loadingSource ? 'Reading…' : 'Load source'}
        </button>
      </div>
      {assessment && <p role="status" className="rounded-lg bg-amber-50 p-3 text-xs text-amber-900">
        Source PO {assessment.po_number} is {assessment.assertion_status}; no valuation is certified.
        Previously allocated inbound charge total: {assessment.currency} {assessment.landed_cost_total}.
        That amount is NOT added to this preview. Itemize its components before relying on the result.
        Damaged and quarantined stock is excluded from saleable cases; reconcile any loss separately.
      </p>}
    </div>
    <form onSubmit={submit} className="space-y-5">
      <div className="grid gap-3 rounded-xl border border-slate-200 bg-white p-4 sm:grid-cols-2">
        <label className="text-xs text-slate-600">Shipment or PO reference
          <input name="shipment_ref" value={shipment} onChange={(e) => setShipment(e.target.value)}
            className={`mt-1 ${STYLE}`} /></label>
        <label className="text-xs text-slate-600">Target contribution margin (%)
          <input name="target_contribution_margin" value={target} onChange={(e) => setTarget(e.target.value)}
            type="number" min="0" max="99" step="0.1" className={`mt-1 ${STYLE}`} /></label>
      </div>
      <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">Saleable products (not damaged or quarantined)</h2>
        {products.map((row, i) => <div key={i} className="rounded-lg border border-slate-200 p-3">
          <div className="mb-2 flex justify-between gap-2">
            <strong className="text-xs">SKU {i + 1}</strong>
            {products.length > 1 && <button type="button" className="text-xs underline"
              onClick={() => setProducts((rows) => rows.filter((_, j) => j !== i))}>Remove</button>}
          </div>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            {([
              ['sku', 'SKU'], ['accepted_cases', 'Accepted cases'],
              ['product_cost_per_case', 'Product cost/case'],
              ['weight_kg', 'Total weight (kg)'],
              ['volume_cbm', 'Total volume (m³)'],
              ['pallet_days', 'Total pallet-days'],
              ['picks', 'Total picks'],
              ['discount_per_case', 'Discount/case'],
              ['returns_allowance_per_case', 'Returns allowance/case'],
              ['commission_per_case', 'Commission/case'],
              ['marketing_per_case', 'Marketing/case'],
            ] as const).map(([key, label]) =>
              <label key={key} className="text-xs text-slate-600">{label}
                <input aria-label={`${label} SKU ${i + 1}`} value={row[key]}
                  onChange={(e) => updateProduct(i, key, e.target.value)}
                  className={`mt-1 ${STYLE}`} />
              </label>)}
          </div>
        </div>)}
        <button type="button" className="text-sm font-medium underline"
          onClick={() => setProducts((rows) => [...rows, blankProduct()])}>Add SKU</button>
      </section>
      <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">Shared logistics and warehouse invoice lines</h2>
        <p className="text-xs text-slate-500">
          Port-to-warehouse drayage is inbound; customer delivery is outbound.
          Each expense line must be unique, referenced and allocated once. Storage uses pallet-days only.
        </p>
        {expenses.map((line, i) => <div key={i} className="grid gap-2 rounded-lg border border-slate-200 p-3 sm:grid-cols-3">
          <label className="text-xs text-slate-600">Invoice line ID
            <input aria-label={`Invoice line ID ${i + 1}`} value={line.expense_line_ref}
              onChange={(e) => updateExpense(i, 'expense_line_ref', e.target.value)}
              className={`mt-1 ${STYLE}`} /></label>
          <label className="text-xs text-slate-600">Cost category
            <select aria-label={`Cost category ${i + 1}`} value={line.component}
              onChange={(e) => {
                updateExpense(i, 'component', e.target.value);
                if (e.target.value === 'warehouse_storage') updateExpense(i, 'allocation_basis', 'pallet_days');
              }}
              className={`mt-1 ${STYLE}`}>
              {COSTS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select></label>
          <label className="text-xs text-slate-600">Total USD (invoice line)
            <input aria-label={`Invoice amount ${i + 1}`} value={line.amount}
              onChange={(e) => updateExpense(i, 'amount', e.target.value)}
              className={`mt-1 ${STYLE}`} /></label>
          <label className="text-xs text-slate-600">Allocation basis
            <select aria-label={`Allocation basis ${i + 1}`}
              value={line.component === 'warehouse_storage' ? 'pallet_days' : line.allocation_basis}
              onChange={(e) => updateExpense(i, 'allocation_basis', e.target.value)}
              disabled={line.component === 'warehouse_storage'} className={`mt-1 ${STYLE}`}>
              {BASES.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
            </select></label>
          <label className="text-xs text-slate-600">Document reference
            <input aria-label={`Document reference ${i + 1}`} value={line.evidence_ref}
              onChange={(e) => updateExpense(i, 'evidence_ref', e.target.value)}
              className={`mt-1 ${STYLE}`} /></label>
          <label className="text-xs text-slate-600">Document revision/date
            <input aria-label={`Document revision ${i + 1}`} value={line.evidence_version}
              onChange={(e) => updateExpense(i, 'evidence_version', e.target.value)}
              className={`mt-1 ${STYLE}`} /></label>
          {expenses.length > 1 && <button type="button" className="text-xs underline"
            onClick={() => setExpenses((rows) => rows.filter((_, j) => j !== i))}>Remove charge</button>}
        </div>)}
        <button type="button" className="text-sm font-medium underline"
          onClick={() => {
            setExpenses((rows) => [...rows, blankExpense(counter)]);
            setCounter((n) => n + 1);
          }}>Add expense line</button>
      </section>
      <button type="submit" disabled={busy} className={BUTTON}>
        {busy ? 'Allocating…' : 'Calculate full cost per case'}
      </button>
    </form>
    {error && <ErrorState message={error} />}
    {result && <section aria-label="Shipment pricing results" className="space-y-3">
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <p className="text-sm font-semibold">Allocated expenses: USD {result.expenses_total}</p>
        <p className="mt-2 text-xs text-amber-800">
          Advisory only. All invoice lines are user supplied; not reconciled with supplier or warehouse invoices.
          No selling price was written.
        </p>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-slate-600"><tr>
              <th className="p-2">SKU</th><th className="p-2">Saleable cases</th>
              <th className="p-2">Inbound / case</th><th className="p-2">Warehouse / case</th>
              <th className="p-2">Fully loaded / case</th>
              <th className="p-2">Min. list price at target</th>
            </tr></thead>
            <tbody>{result.products.map((p) => <tr key={p.sku} className="border-t">
              <td className="p-2 font-medium">{p.sku}</td>
              <td className="p-2">{p.accepted_cases}</td>
              <td className="p-2">${p.inbound_cost_per_case}</td>
              <td className="p-2">${p.warehouse_cost_per_case}</td>
              <td className="p-2 font-semibold">${p.fully_loaded_cost_per_case}</td>
              <td className="p-2 font-semibold">${p.minimum_list_price_at_target_contribution}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </div>
      {(result.missing_inbound_components.length > 0 || result.missing_warehouse_components.length > 0)
        && <p role="status" className="rounded-lg bg-amber-50 p-3 text-xs text-amber-900">
          Price is incomplete: missing inbound categories ({result.missing_inbound_components.join(', ') || 'none'})
          and warehouse categories ({result.missing_warehouse_components.join(', ') || 'none'}).
          Unknown costs are not evidence of zero expense.
        </p>}
    </section>}
  </InsightShell>;
}
