import { useEffect, useState } from 'react';
import { api, type PricePostingCaptureReadinessResult } from '../../lib/api';
import { useAuth } from '../../hooks/useAuth';

export function PricePostingCaptureReadinessPanel({
  selectedIds, effectiveDate,
}: { selectedIds: string[]; effectiveDate: string }) {
  const { role, hasPermission } = useAuth();
  const permitted = (role === 'owner' || role === 'manager') && hasPermission('view_pricing');
  const [provider, setProvider] = useState<'qbo' | 'qbd'>('qbo');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<PricePostingCaptureReadinessResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { setResult(null); setError(null); }, [selectedIds, effectiveDate, provider]);

  async function inspect() {
    if (!permitted || loading || !effectiveDate || selectedIds.length === 0) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const response = await api.pricePosting.previewCaptureReadiness({
        provider, effective_date: effectiveDate, price_book_ids: selectedIds,
      });
      if (response.status !== 'capture_prerequisites_only' ||
          response.provider_requested !== provider ||
          response.provider_api_called !== false ||
          response.source_items_observed_live !== false ||
          response.real_provider_connection_verified !== false ||
          response.item_identity_certified !== false ||
          response.case_pack_verified !== false ||
          response.accounting_reconciled !== false ||
          response.safe_to_capture_live !== false ||
          response.observation_saved !== false ||
          response.ready_to_file !== false ||
          response.submission_permitted !== false ||
          response.price_published !== false ||
          !Array.isArray(response.items)) {
        throw new Error('Capture prerequisites were not confirmed as non-authorizing');
      }
      setResult(response);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Capture mapping source unavailable');
    } finally {
      setLoading(false);
    }
  }

  if (!permitted) return null;

  return (
    <section aria-label="QuickBooks item identity mapping readiness"
      className="space-y-3 rounded-lg border border-amber-200 bg-amber-50 p-4">
      <h4 className="text-sm font-semibold text-slate-900">
        QBO/QBD item mapping prerequisites — owner review
      </h4>
      <p className="text-xs text-slate-800">
        Read-only inspection of stored, unverified item-ID and case-pack candidates.
        Does not connect to QuickBooks, save observations, authenticate a realm,
        or authorize prices or agency filings.
      </p>
      <label className="block text-xs font-semibold text-slate-800">
        Proposed source system (not a connection test)
        <select value={provider} onChange={(e) => setProvider(e.target.value as 'qbo' | 'qbd')}
          className="mt-1 block rounded-md border border-slate-300 bg-white p-2">
          <option value="qbo">QuickBooks Online candidate</option>
          <option value="qbd">QuickBooks Desktop candidate</option>
        </select>
      </label>
      <button type="button" disabled={loading || !effectiveDate || !selectedIds.length}
        onClick={() => void inspect()}
        className="rounded-md border border-slate-400 px-3 py-2 text-xs font-semibold disabled:opacity-40">
        {loading ? 'Inspecting candidate bindings…' : 'Inspect mapping prerequisites — no live QB access'}
      </button>
      {error && <p role="alert" className="text-xs text-red-900">
        {error}. Capture authorization remains unavailable.
      </p>}
      {result && (
        <div role="region" aria-label="Unverified QB item identity candidate results"
          className="space-y-2 text-xs text-slate-800">
          <p className="font-semibold">
            {result.not_mapped} missing mappings · {result.ambiguous} ambiguous mappings
          </p>
          <p className="text-amber-900">
            All mappings require independent human identification and provider connection review.
            Live capture is disabled.
          </p>
          {result.items.map((item) => (
            <article key={item.price_book_id}
              className="rounded-md border border-amber-200 bg-white p-3">
              <p className="font-semibold">{item.sku} · {item.provider.toUpperCase()}</p>
              <p>
                Item ID candidate: {item.candidate_provider_item_id_present ? 'recorded, unverified' : 'not established'}
                {' · '}Unit basis: {item.candidate_unit_basis ?? 'unknown'}
                {item.candidate_units_per_case ? ` · ${item.candidate_units_per_case} units/case (candidate)` : ''}
              </p>
              <ul className="mt-2 list-disc space-y-1 pl-4">
                {item.blockers.map((code) => <li key={code}>{code.replace(/_/g, ' ')}</li>)}
              </ul>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
