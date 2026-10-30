import React, { useState } from 'react';
import { api } from '../../lib/api';
import { useAuth } from '../../hooks/useAuth';
import {
  BUTTON_PRIMARY, CARD, ErrorNote, Field, INPUT, ORDER_VIEW_ROLES, WarningNote, dateTimeInputToIso,
  nowForDateTimeInput, parseCases, roleAllowed,
} from './logisticsShared';

type ReturnReview = {
  return_reference: string;
  sku: string;
  requested_cases: number;
  category_candidate: string;
  classification_verified: boolean;
  proposed_disposition: string;
  inspection_routing: string;
  snapshot_stale: boolean;
  snapshot_age_minutes: number;
  pipeline: { stage: string; state: string }[];
  next_action: string;
  disposition_authorized: boolean;
  inventory_posted: boolean;
  credit_issued: boolean;
  supplier_claim_filed: boolean;
};

const CATEGORY_TEXT: Record<string, string> = {
  unclassified: 'Unclear — needs a person to decide',
  defective: 'Defective product',
  wrong_item: 'Wrong item sent',
  overstock: 'Customer overstock',
  seasonal_close: 'End of season',
  customer_cancel: 'Customer cancelled',
  quality_concern: 'Quality concern',
  expired: 'Expired product',
  damaged_in_transit: 'Damaged in transit',
};

const STAGE_TEXT: Record<string, string> = {
  return_request: 'Return request',
  order_and_receipt_match: 'Match to the original order',
  physical_quarantine: 'Set aside in quarantine',
  independent_inspection: 'Independent inspection',
  disposition_approval: 'Decision approved',
  stock_or_credit_posting: 'Stock or credit updated',
  supplier_claim_and_audit: 'Supplier claim and audit',
};

const STATE_TEXT: Record<string, string> = {
  unverified_caller_preview: 'Entered here, not verified',
  not_verified: 'Not verified yet',
  not_authorized: 'Not approved',
  not_performed: 'Not done',
};

const PRODUCT_TYPES: Record<string, string> = {
  beverage: 'Beverage', food: 'Food', nonperishable: 'Non-perishable goods', other: 'Other',
};

type FormState = Record<'return_reference' | 'order_reference' | 'warehouse_id' | 'sku' | 'customer_id'
  | 'supplier_id' | 'lot_code' | 'requested_cases' | 'product_type' | 'return_reason', string>;

const EMPTY: FormState = {
  return_reference: '', order_reference: '', warehouse_id: '', sku: '', customer_id: '', supplier_id: '',
  lot_code: '', requested_cases: '', product_type: '', return_reason: '',
};

const FIELDS: { key: keyof FormState; label: string; max: number; optional?: boolean }[] = [
  { key: 'return_reference', label: 'Return reference', max: 150 },
  { key: 'order_reference', label: 'Original order reference', max: 150 },
  { key: 'customer_id', label: 'Customer', max: 150 },
  { key: 'supplier_id', label: 'Supplier', max: 150 },
  { key: 'sku', label: 'SKU', max: 120 },
  { key: 'lot_code', label: 'Lot code (optional)', max: 120, optional: true },
  { key: 'warehouse_id', label: 'Warehouse', max: 150 },
];

/** Review a customer return (POST /api/logistics/returns/preview). Nothing is approved or posted. */
export function ReturnPreview() {
  const { role } = useAuth();
  const [form, setForm] = useState<FormState>(EMPTY);
  const [observedAt, setObservedAt] = useState(() => nowForDateTimeInput());
  const [problem, setProblem] = useState('');
  const [result, setResult] = useState<ReturnReview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  if (!roleAllowed(role, ORDER_VIEW_ROLES)) {
    return <p className="p-4 sm:p-6 text-sm text-slate-600">Return reviews are not available for your role.</p>;
  }

  function set(key: keyof FormState, value: string) {
    setForm((previous) => ({ ...previous, [key]: value }));
  }

  async function submit() {
    setResult(null);
    setError(null);
    for (const field of FIELDS) {
      const value = form[field.key].trim();
      if ((!value && !field.optional) || value.length > field.max) { setProblem(`Enter the ${field.label.toLowerCase()}.`); return; }
    }
    const cases = parseCases(form.requested_cases, { allowZero: false });
    if (cases === null) { setProblem('Enter the number of cases being returned (at least 1).'); return; }
    if (!PRODUCT_TYPES[form.product_type]) { setProblem('Choose the product type.'); return; }
    const reason = form.return_reason.trim();
    if (!reason || reason.length > 1500) { setProblem('Describe why the customer is returning it.'); return; }
    const observed = dateTimeInputToIso(observedAt);
    if (!observed) { setProblem('Enter when the return was reported.'); return; }
    setProblem('');
    setLoading(true);
    try {
      const data = await api.post<ReturnReview>('/api/logistics/returns/preview', {
        return_reference: form.return_reference.trim(),
        order_reference: form.order_reference.trim(),
        warehouse_id: form.warehouse_id.trim(),
        sku: form.sku.trim(),
        customer_id: form.customer_id.trim(),
        supplier_id: form.supplier_id.trim(),
        lot_code: form.lot_code.trim(),
        requested_cases: cases,
        unit: 'case',
        product_type: form.product_type,
        return_reason: reason,
        observed_at: observed,
      });
      if (!data || typeof data.category_candidate !== 'string' || !Array.isArray(data.pipeline)) {
        throw new Error('Return review is unavailable.');
      }
      setResult(data);
    } catch (reason) {
      setError(reason);
    } finally {
      setLoading(false);
    }
  }

  return <section className="p-4 sm:p-6 space-y-4" aria-label="Return review">
    <form className={CARD} aria-label="Review a customer return" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <h3 className="font-semibold text-slate-900">Review a customer return</h3>
      <p className="text-sm text-slate-600">
        Enter a return to see how it would be classified and what should happen next. Nothing is approved, no stock
        or credit is posted, and the supplier is not contacted.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {FIELDS.map((field) => <Field key={field.key} label={field.label} htmlFor={`return-${field.key}`}>
          <input id={`return-${field.key}`} className={INPUT} maxLength={field.max} value={form[field.key]}
            onChange={(event) => set(field.key, event.target.value)} />
        </Field>)}
        <Field label="Cases being returned" htmlFor="return-requested_cases">
          <input id="return-requested_cases" className={INPUT} inputMode="numeric" value={form.requested_cases}
            onChange={(event) => set('requested_cases', event.target.value)} />
        </Field>
        <Field label="Product type" htmlFor="return-product_type">
          <select id="return-product_type" className={INPUT} value={form.product_type}
            onChange={(event) => set('product_type', event.target.value)}>
            <option value="">Choose a type</option>
            {Object.entries(PRODUCT_TYPES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </Field>
        <Field label="Reported at" htmlFor="return-observed">
          <input id="return-observed" type="datetime-local" className={INPUT} value={observedAt}
            onChange={(event) => setObservedAt(event.target.value)} />
        </Field>
      </div>
      <Field label="Reason for the return" htmlFor="return-return_reason">
        <textarea id="return-return_reason" className={INPUT} rows={3} maxLength={1500} value={form.return_reason}
          onChange={(event) => set('return_reason', event.target.value)} />
      </Field>
      {problem && <p role="alert" className="text-sm text-red-700">{problem}</p>}
      <button type="submit" className={BUTTON_PRIMARY} disabled={loading}>{loading ? 'Reviewing…' : 'Review return'}</button>
      {Boolean(error) && <ErrorNote error={error} fallback="Unable to review the return." />}
    </form>
    {result && <article className={CARD} aria-label="Return review result">
      <h4 className="font-semibold text-slate-900">Return {result.return_reference} · {result.requested_cases} cases of {result.sku}</h4>
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
        <div><dt className="text-slate-600">Likely reason (suggested)</dt>
          <dd className="font-semibold">{CATEGORY_TEXT[result.category_candidate] ?? result.category_candidate}</dd></div>
        <div><dt className="text-slate-600">Recommended handling</dt>
          <dd className="font-semibold">{result.proposed_disposition === 'hold_for_inspection'
            ? 'Hold the goods for inspection' : result.proposed_disposition.replace(/_/g, ' ')}</dd></div>
      </dl>
      <p className="text-sm">
        Next step: match the return to the original order and lot, set the goods aside in quarantine, then have them
        inspected by someone independent before any restock, credit or supplier claim.
      </p>
      {result.snapshot_stale && <WarningNote>This return was reported more than an hour ago; check the details are still current.</WarningNote>}
      <Table stages={result.pipeline} />
      <p className="text-xs text-slate-600">
        The classification is a suggestion from the reason text and has not been verified. No decision was approved,
        stock was not changed, no credit was issued and no supplier claim was filed.
      </p>
    </article>}
  </section>;
}

function Table({ stages }: { stages: { stage: string; state: string }[] }) {
  return <div className="overflow-x-auto"><table className="w-full text-sm text-left">
    <thead><tr className="text-slate-600"><th className="p-2">Step</th><th className="p-2">Status</th></tr></thead>
    <tbody>{stages.map((item) => <tr key={item.stage} className="border-t border-slate-200">
      <td className="p-2">{STAGE_TEXT[item.stage] ?? item.stage}</td>
      <td className="p-2">{STATE_TEXT[item.state] ?? item.state}</td>
    </tr>)}</tbody>
  </table></div>;
}
