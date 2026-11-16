import { useEffect, useState } from 'react';
import { api, type PricePostingAccountingComparisonResult } from '../../lib/api';

function readable(code: string) {
  return code.replace(/_/g, ' ');
}

/**
 * A numerical comparison of stored, unverified evidence. NOT a provider
 * connection, accountant's certification, regulatory review or price filing.
 */
export function PricePostingAccountingComparisonPanel({
  selectedIds,
}: { selectedIds: string[] }) {
  const [result, setResult] = useState<PricePostingAccountingComparisonResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setResult(null);
    setError(null);
  }, [selectedIds]);

  async function compare() {
    if (!selectedIds.length || loading) return;
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      const response = await api.pricePosting.compareAccounting({ price_book_ids: selectedIds });
      if (response.status !== 'internal_numeric_comparison_only' ||
          response.independent_qbo_qbd_reconciliation_confirmed !== false ||
          response.warehouse_cost_coverage_verified !== false ||
          response.regulatory_requirements_verified !== false ||
          response.qbo_qbd_live_data_accessed !== false ||
          response.ready_to_file !== false ||
          response.submission_permitted !== false ||
          response.agency_template_generated !== false ||
          response.live_price_changed !== false ||
          !Array.isArray(response.checks)) {
        throw new Error('Accounting comparison is not confirmed as non-certifying');
      }
      setResult(response);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Accounting evidence unavailable');
    } finally {
      setLoading(false);
    }
  }

  return (
    <section aria-label="Stored accounting observation comparison"
      className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
      <h4 className="text-sm font-semibold text-slate-900">
        QuickBooks observation comparison — internal only
      </h4>
      <p className="text-xs text-slate-700">
        Compares saved QBO/QBD observation amounts with selected case prices using
        their explicit unit basis. It does not connect to QuickBooks or prove
        that the observations are authentic, complete, current or jurisdiction-specific.
      </p>
      <button type="button" disabled={loading || selectedIds.length === 0}
        onClick={() => void compare()}
        className="rounded-md border border-slate-400 px-3 py-2 text-xs font-semibold disabled:opacity-40">
        {loading ? 'Checking recorded observations…' :
          'Compare recorded observations — no live QB access'}
      </button>
      {error && <p role="alert" className="text-xs text-red-800">
        {error}. No accounting reconciliation was confirmed.
      </p>}
      {result && (
        <div role="region" aria-label="Unverified accounting comparison results"
          className="space-y-2 text-xs text-slate-700">
          <p className="font-semibold">
            {result.numeric_matches_unverified} numeric matches (unverified) ·
            {' '}{result.numeric_differences} differences ·
            {' '}{result.uncomparable} not comparable
          </p>
          <p className="font-medium text-amber-800">
            Independent accounting reconciliation: not established. Filing disabled.
          </p>
          {result.checks.map((item) => (
            <article key={item.price_book_id} className="rounded-md border border-slate-200 p-3">
              <p className="font-semibold">{item.sku} · {readable(item.numeric_comparison)}</p>
              <p>
                Book: {item.book_amount_per_case} / case · Recorded accounting:
                {' '}{item.observed_amount_per_case ?? 'not comparable'}
                {item.provider ? ` · ${item.provider.toUpperCase()}` : ''}
              </p>
              <ul className="list-disc space-y-1 pl-4">
                {item.blockers.map((code) => <li key={code}>{readable(code)}</li>)}
              </ul>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
