import { useCallback, useEffect, useState } from 'react';
import { api, type PriceBookSourceItem, type PricePostingReadinessResult, type PricePostingRuleProgram } from '../../lib/api';
import { useAuth } from '../../hooks/useAuth';
import { PricePostingAccountingComparisonPanel } from './PricePostingAccountingComparisonPanel';

function readableReason(reason: string): string {
  return reason.replace(/_/g, ' ').replace(/:/g, ': ');
}

export function PricePostingReadinessPanel({ programs }: { programs: PricePostingRuleProgram[] }) {
  const { hasPermission } = useAuth();
  const canViewPricing = hasPermission('view_pricing');
  const [prices, setPrices] = useState<PriceBookSourceItem[]>([]);
  const [partialList, setPartialList] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sourceFailure, setSourceFailure] = useState<string | null>(null);
  const [programKey, setProgramKey] = useState('');
  const [effectiveDate, setEffectiveDate] = useState('');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [result, setResult] = useState<PricePostingReadinessResult | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const loadSources = useCallback(async () => {
    setLoading(true);
    setSourceFailure(null);
    setPrices([]);
    setResult(null);
    try {
      const source = await api.pricePosting.listSourcePrices();
      if (!source || !Array.isArray(source.items)
          || source.status !== 'analysis_only'
          || source.source !== 'connected_data'
          || source.price_changes_allowed !== false
          || source.cost_source_certified !== false) {
        throw new Error('Pricing evidence could not be confirmed as read-only');
      }
      setPrices(source.items);
      setPartialList(source.next_offset !== null);
    } catch (err) {
      setSourceFailure(err instanceof Error ? err.message : 'Price-book source unavailable');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (canViewPricing) void loadSources();
  }, [canViewPricing, loadSources]);

  function toggleId(id: string) {
    setResult(null);
    setReviewError(null);
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((value) => value !== id)
        : prev.length >= 50 ? prev : [...prev, id]);
  }

  async function checkReadiness() {
    if (!programKey || !effectiveDate || !selectedIds.length || checking) return;
    setChecking(true);
    setReviewError(null);
    setResult(null);
    try {
      const preview = await api.pricePosting.previewReadiness({
        program_key: programKey,
        price_book_ids: selectedIds,
        effective_date: effectiveDate,
      });
      if (!preview || preview.ready_to_file !== false
          || preview.submission_permitted !== false
          || preview.agency_template_generated !== false
          || preview.live_price_changed !== false
          || preview.qbo_qbd_live_data_accessed !== false
          || !Array.isArray(preview.blockers)
          || !Array.isArray(preview.source_evidence)) {
        throw new Error('Readiness output was not verified as non-submittable');
      }
      setResult(preview);
    } catch (err) {
      setReviewError(err instanceof Error ? err.message : 'Readiness evidence unavailable');
    } finally {
      setChecking(false);
    }
  }

  if (!canViewPricing) {
    return (
      <div role="note" className="rounded-lg border border-slate-200 p-4 text-sm text-slate-700">
        Price-book evidence is restricted to authorized Pricing users.
        This does not indicate clearance or a completed filing.
      </div>
    );
  }

  return (
    <section aria-label="Internal price posting source readiness" className="space-y-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
      <div>
        <h3 className="text-base font-semibold text-slate-900">Internal source reconciliation</h3>
        <p className="mt-1 text-sm text-slate-600">
          Select recorded price-book revisions to check approval, landed-cost evidence,
          product classification and license records. This does not use live QuickBooks,
          certify a state requirement, or file a price list.
        </p>
      </div>
      <div role="note" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
        Filing disabled. An internally reconciled cost snapshot does not establish
        independent QBO/QBD agreement, regulatory approval or complete warehouse costs.
      </div>
      {sourceFailure && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900">
          Pricing source unavailable: {sourceFailure}.
          <button type="button" className="ml-2 underline" onClick={() => void loadSources()}>Retry</button>
        </div>
      )}
      {loading && <p role="status" className="text-sm text-slate-600">Checking source price book…</p>}
      {!loading && !sourceFailure && prices.length === 0 && (
        <p role="status" className="text-sm text-slate-600">
          No price-book source revisions were returned. No price-posting assessment was made.
        </p>
      )}
      {partialList && !sourceFailure && (
        <p role="note" className="text-xs text-amber-800">
          Showing only the first 100 source records. Further price-book pages were not assessed.
        </p>
      )}
      {!loading && !sourceFailure && prices.length > 0 && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm font-medium text-slate-800">
              Jurisdiction and program
              <select value={programKey} onChange={(event) => {
                setProgramKey(event.target.value);
                setResult(null);
              }} className="mt-1 w-full rounded-md border border-slate-300 bg-white p-2">
                <option value="">Select a rule program</option>
                {programs.map((rule) => (
                  <option key={rule.id} value={rule.program_key}>
                    {rule.region_code} — {rule.program_name} (v{rule.version})
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm font-medium text-slate-800">
              Effective date to evaluate
              <input type="date" value={effectiveDate} onChange={(event) => {
                setEffectiveDate(event.target.value);
                setResult(null);
              }} className="mt-1 w-full rounded-md border border-slate-300 bg-white p-2" />
            </label>
          </div>
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold text-slate-800">
              Existing price-book revisions ({selectedIds.length} selected; limit 50)
            </legend>
            <div className="max-h-64 space-y-2 overflow-y-auto rounded-md border border-slate-200 bg-white p-3">
              {prices.map((price) => (
                <label key={price.id} className="flex gap-3 border-b border-slate-100 pb-2 text-sm last:border-0">
                  <input type="checkbox" checked={selectedIds.includes(price.id)}
                    disabled={selectedIds.length >= 50 && !selectedIds.includes(price.id)}
                    onChange={() => toggleId(price.id)} aria-label={`Select ${price.sku} price-book revision ${price.revision}`} />
                  <span>
                    <strong className="text-slate-900">{price.sku}</strong>
                    {' · '}{price.market_code} · {price.package_label}
                    {' · '}{price.currency} {price.list_price_per_case} / case
                    <span className="block text-xs text-slate-500">
                      Revision {price.revision} · {price.status} · effective {price.effective_from}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <button type="button" disabled={!selectedIds.length || !programKey || !effectiveDate || checking}
            onClick={() => void checkReadiness()}
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40">
            {checking ? 'Checking sources…' : 'Check internal evidence — no filing'}
          </button>
        </>
      )}
      {reviewError && (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900">
          Evidence assessment unavailable: {reviewError}. No compliance clearance may be inferred.
        </div>
      )}
      {result && (
        <div className="space-y-3" role="region" aria-label="Blocked price posting readiness findings">
          <h4 className="font-semibold text-slate-900">
            {result.region_code} — Assessment blocked pending verification
          </h4>
          <p className="text-sm text-slate-700">
            Documentary rule review: {result.human_rule_review_recorded ? 'recorded' : 'not recorded'}.
            Filing disabled. No QuickBooks data was accessed or modified.
          </p>
          <p className="text-sm font-semibold text-amber-900">
            {result.blockers.length} unresolved checks. No source record is authorization to submit.
          </p>
          <ul className="max-h-60 list-disc space-y-1 overflow-y-auto pl-5 text-xs text-slate-700">
            {result.blockers.map((code, index) => <li key={`${code}-${index}`}>{readableReason(code)}</li>)}
          </ul>
          <div className="space-y-2">
            {result.source_evidence.map((evidence) => (
              <article key={evidence.price_book_id} className="rounded-md border border-slate-200 bg-white p-3 text-xs">
                <strong>{evidence.sku}</strong>
                <span className="ml-2 text-slate-600">
                  Internal cost reconciled: {evidence.cost_basis_recorded_as_reconciled ? 'recorded' : 'not confirmed'}
                  {' · '}Possible license records: {evidence.matching_license_record_count}
                </span>
                <p className="mt-1 text-slate-600">
                  Accounting and regulator verification: not independently established.
                </p>
              </article>
            ))}
          </div>
          <PricePostingAccountingComparisonPanel selectedIds={selectedIds} />
        </div>
      )}
    </section>
  );
}
