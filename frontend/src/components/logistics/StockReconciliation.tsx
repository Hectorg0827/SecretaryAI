import React, { useState } from 'react';
import { api } from '../../lib/api';
import { useAuth } from '../../hooks/useAuth';
import {
  BUTTON_PRIMARY, CARD, ErrorNote, Field, INPUT, PURCHASING_ROLES, WarningNote, formatDateTime, roleAllowed,
} from './logisticsShared';

type Reconciliation = {
  sku: string;
  product_name: string;
  unit: string;
  ledger_record_count: number;
  ledger_quantities: { available: number; damaged: number; quarantined: number };
  ledger_total: number;
  latest_ledger_update: string | null;
  qb_quantity: number | null;
  indicative_quantity_difference: number | null;
  state: string;
  reconciled: boolean;
  scope_warning: string;
};

const STATE_TEXT: Record<string, string> = {
  missing_explicit_qb_mapping: 'This product is not linked to a QuickBooks item, so no comparison was made.',
  qb_item_not_found: 'The linked QuickBooks item was not found, so no comparison was made.',
  ambiguous_qb_mapping: 'More than one QuickBooks item matched the link, so no comparison was made.',
  unverified_scope: 'Compared with QuickBooks, but the two systems may not cover the same locations or stock.',
};

/** Read-only comparison of the stock ledger with QuickBooks for one SKU. */
export function StockReconciliation() {
  const { role } = useAuth();
  const [sku, setSku] = useState('');
  const [result, setResult] = useState<Reconciliation | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [problem, setProblem] = useState('');
  const [loading, setLoading] = useState(false);

  if (!roleAllowed(role, PURCHASING_ROLES)) return null;

  async function lookUp() {
    const code = sku.trim();
    setResult(null);
    setError(null);
    if (!code || code.length > 120) { setProblem('Enter a SKU (up to 120 characters).'); return; }
    setProblem('');
    setLoading(true);
    try {
      const data = await api.get<Reconciliation>(`/api/logistics/reconciliation/stock/${encodeURIComponent(code)}`);
      if (!data?.ledger_quantities || typeof data.state !== 'string') throw new Error('Stock comparison is unavailable.');
      setResult(data);
    } catch (reason) {
      setError(reason);
    } finally {
      setLoading(false);
    }
  }

  return <section aria-label="Stock check against QuickBooks" className="space-y-3">
    <div>
      <h3 className="font-semibold text-slate-900">Stock check against QuickBooks</h3>
      <p className="text-sm text-slate-600">
        Compares the stock SecretaryAI has booked from approved deliveries with QuickBooks. Read-only: nothing is changed in either system.
      </p>
    </div>
    <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); void lookUp(); }}>
      <div className="flex-1 min-w-[12rem]">
        <Field label="SKU" htmlFor="reconcile-sku">
          <input id="reconcile-sku" className={INPUT} value={sku} maxLength={120} onChange={(event) => setSku(event.target.value)} />
        </Field>
      </div>
      <button type="submit" className={BUTTON_PRIMARY} disabled={loading}>{loading ? 'Checking…' : 'Check stock'}</button>
    </form>
    {problem && <p role="alert" className="text-sm text-red-700">{problem}</p>}
    {Boolean(error) && <ErrorNote error={error} fallback="Unable to compare stock." />}
    {result && <article className={CARD}>
      <h4 className="font-semibold text-slate-900">{result.sku} · {result.product_name}</h4>
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
        <div><dt className="text-slate-600">Available ({result.unit}s)</dt><dd className="font-semibold">{result.ledger_quantities.available}</dd></div>
        <div><dt className="text-slate-600">Damaged</dt><dd className="font-semibold">{result.ledger_quantities.damaged}</dd></div>
        <div><dt className="text-slate-600">Held for inspection</dt><dd className="font-semibold">{result.ledger_quantities.quarantined}</dd></div>
        <div><dt className="text-slate-600">SecretaryAI total</dt><dd className="font-semibold">{result.ledger_total}</dd></div>
        <div><dt className="text-slate-600">QuickBooks quantity</dt><dd className="font-semibold">{result.qb_quantity ?? 'Not compared'}</dd></div>
        <div><dt className="text-slate-600">Difference (QuickBooks − SecretaryAI)</dt>
          <dd className="font-semibold">{result.indicative_quantity_difference ?? 'Not compared'}</dd></div>
        <div><dt className="text-slate-600">Last stock update</dt><dd>{formatDateTime(result.latest_ledger_update)}</dd></div>
        <div><dt className="text-slate-600">Reconciled</dt><dd>{result.reconciled ? 'Yes' : 'No'}</dd></div>
      </dl>
      <p className="text-sm text-slate-800">{STATE_TEXT[result.state] ?? result.state}</p>
      <WarningNote>{result.scope_warning}</WarningNote>
    </article>}
  </section>;
}
