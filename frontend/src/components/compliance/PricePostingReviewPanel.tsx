import { useCallback, useEffect, useState } from 'react';
import {
  api,
  type PricePostingRuleProgram,
  type PricePostingHumanReviewInput,
} from '../../lib/api';
import { useAuth } from '../../hooks/useAuth';

const blankChecks = {
  source: false,
  applicability: false,
  noFiling: false,
};

export function PricePostingReviewPanel() {
  const { role } = useAuth();
  const isReviewer = role === 'owner' || role === 'manager';
  const [programs, setPrograms] = useState<PricePostingRuleProgram[]>([]);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [evidenceRef, setEvidenceRef] = useState('');
  const [reviewNote, setReviewNote] = useState('');
  const [checks, setChecks] = useState(blankChecks);
  const [submitting, setSubmitting] = useState(false);
  const [receipt, setReceipt] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const response = await api.pricePosting.listPrograms();
      if (!response || !Array.isArray(response.programs)
          || response.agency_submission_enabled !== false) {
        throw new Error('Price posting rules could not be verified');
      }
      setPrograms(response.programs);
      setFailure(null);
    } catch (error) {
      setPrograms([]);
      setFailure(error instanceof Error ? error.message : 'Regulatory rule source unavailable');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selected = programs.find((rule) => rule.id === selectedId) ?? null;
  const canSubmit = Boolean(
    isReviewer && selected && !selected.human_verified_for_tenant
    && !submitting && checks.source && checks.applicability && checks.noFiling
    && evidenceRef.trim().length >= 10 && reviewNote.trim().length >= 30,
  );

  function choose(ruleId: string) {
    setSelectedId((id) => id === ruleId ? null : ruleId);
    setEvidenceRef('');
    setReviewNote('');
    setChecks({ ...blankChecks });
    setReceipt(null);
    setFailure(null);
  }

  async function recordHumanReview() {
    if (!selected || !canSubmit) return;
    const body: PricePostingHumanReviewInput = {
      rule_id: selected.id,
      version: selected.version,
      evidence_ref: evidenceRef.trim(),
      review_note: reviewNote.trim(),
      checked_current_official_source: checks.source,
      checked_tenant_license_applicability: checks.applicability,
      acknowledged_no_filing_authority: checks.noFiling,
    };
    setSubmitting(true);
    setFailure(null);
    setReceipt(null);
    try {
      const result = await api.pricePosting.recordHumanReview(selected.program_key, body);
      if (result.state !== 'documentary_review_recorded'
          || result.submission_permitted !== false
          || !result.review_id || !result.audit_event_id) {
        throw new Error('Review and audit receipt could not be verified');
      }
      setReceipt(result.review_id);
      setSelectedId(null);
      setEvidenceRef('');
      setReviewNote('');
      setChecks({ ...blankChecks });
      await refresh();
    } catch (error) {
      setFailure(error instanceof Error ? error.message : 'Human review was not recorded');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="p-6 space-y-4" aria-label="Price posting regulatory source review">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Price Posting — NY, NJ and FL</h2>
        <p className="mt-1 text-sm text-slate-600">
          Review candidate regulatory sources and record supporting evidence. This is not
          price publication, government filing, or confirmation of regulatory clearance.
        </p>
      </div>

      <div role="note" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        Agency submissions are disabled. Current filing dates, applicable licenses,
        quantity definitions, discounts and prescribed templates require separate review.
      </div>

      {receipt && (
        <p role="status" className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
          Documentary source review recorded with an audit receipt: {receipt}.
          Filing permission remains disabled.
        </p>
      )}
      {failure && (
        <div role="alert" className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900">
          {failure}. No filing or price change has been authorized.
          <button type="button" onClick={() => void refresh()} className="ml-2 underline font-medium">
            Retry source check
          </button>
        </div>
      )}

      {loading && <p role="status" className="text-sm text-slate-600">Checking regulatory rule sources…</p>}
      {!loading && !failure && programs.length === 0 && (
        <p className="text-sm text-slate-600">No rule versions are available. No filing determination was made.</p>
      )}

      {!loading && programs.map((rule) => (
        <article key={rule.id} className="rounded-xl border border-slate-200 bg-white px-4 py-4 space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                {rule.region_code} · Version {rule.version}
              </p>
              <h3 className="font-semibold text-slate-900">{rule.program_name}</h3>
              <p className="mt-1 text-xs text-slate-600">{rule.applicability_note}</p>
            </div>
            <span className="text-xs font-semibold text-slate-700">
              {rule.human_verified_for_tenant ? 'Documentary review recorded' : 'Human review required'}
            </span>
          </div>
          <p className="text-sm">
            <a href={rule.authority_url} target="_blank" rel="noopener noreferrer"
              className="text-blue-700 underline">
              Open official source: {rule.authority_title}
            </a>
          </p>
          <p className="text-xs text-amber-800">
            {rule.filing_deadline_rule == null ? 'Filing deadline not verified' : 'Deadline rule requires independent review'}
            {' · '}
            {rule.official_template_url == null ? 'Official template not verified' : 'Template requires independent review'}
          </p>
          {isReviewer && !rule.human_verified_for_tenant && (
            <button type="button" onClick={() => choose(rule.id)}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50">
              {selectedId === rule.id ? 'Close source review' : 'Review source and applicability'}
            </button>
          )}
          {selectedId === rule.id && !rule.human_verified_for_tenant && isReviewer && (
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 space-y-3">
              <p className="text-sm text-slate-700">
                Confirm this exact rule version against current regulator guidance. Your
                tenant policy may still require a separate approval before recording the review.
              </p>
              <label htmlFor="rule-evidence" className="block text-sm font-medium text-slate-800">
                Official source link or retained document reference
              </label>
              <input id="rule-evidence" type="text" value={evidenceRef}
                onChange={(event) => setEvidenceRef(event.target.value)}
                maxLength={1500} className="w-full rounded-md border border-slate-300 bg-white p-2 text-sm"
                placeholder="https://regulator.example/... or internal evidence reference" />
              <label htmlFor="rule-notes" className="block text-sm font-medium text-slate-800">
                Reviewer notes: source date, license applicability and unresolved items
              </label>
              <textarea id="rule-notes" rows={4} value={reviewNote}
                onChange={(event) => setReviewNote(event.target.value)}
                maxLength={4000} className="w-full rounded-md border border-slate-300 bg-white p-2 text-sm"
                placeholder="Explain the official source, tenant scope, verification date and exceptions…" />
              <label className="flex items-start gap-2 text-sm text-slate-800">
                <input type="checkbox" checked={checks.source}
                  onChange={(event) => setChecks((v) => ({ ...v, source: event.target.checked }))} />
                I independently checked the current official authority for this program/version.
              </label>
              <label className="flex items-start gap-2 text-sm text-slate-800">
                <input type="checkbox" checked={checks.applicability}
                  onChange={(event) => setChecks((v) => ({ ...v, applicability: event.target.checked }))} />
                I checked this tenant's license and regulatory applicability.
              </label>
              <label className="flex items-start gap-2 text-sm text-slate-800">
                <input type="checkbox" checked={checks.noFiling}
                  onChange={(event) => setChecks((v) => ({ ...v, noFiling: event.target.checked }))} />
                I understand this records documentary review only; it does not authorize filing.
              </label>
              <button type="button" disabled={!canSubmit} onClick={() => void recordHumanReview()}
                className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40">
                {submitting ? 'Recording audit…' : 'Record human source review'}
              </button>
            </div>
          )}
        </article>
      ))}
      {!isReviewer && (
        <p className="text-xs text-slate-500">
          Read-only access. Only an authorized owner or manager may record documentary verification.
        </p>
      )}
    </section>
  );
}
