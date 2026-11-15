import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import './lola-pricing-evidence.css';

type Candidate = {
  cost_snapshot_id: string;
  sku: string;
  shipment_ref: string;
  source_evidence_status: 'provisional' | 'reconciled';
  source_cost_as_of: string;
};
type CandidateList = {
  state: 'snapshot_candidates' | 'no_pricing_snapshots_recorded';
  items: Candidate[];
  has_more: boolean;
  accounting_reconciled: false;
  price_changes_allowed: false;
};
type PriceReview = {
  cost_snapshot_id: string;
  currency: string;
  recorded_cost_per_case: string | null;
  source_documentation_complete: boolean;
  issues_requiring_review: string[];
  accounting_reconciled: false;
  gross_margin_calculated: false;
  price_publication_verified: false;
  external_writes_executed: false;
};
function record(x: unknown): x is Record<string, unknown> {
  return x !== null && typeof x === 'object' && !Array.isArray(x);
}
function validCandidates(x: unknown): x is CandidateList {
  return record(x) && (x.state === 'snapshot_candidates' || x.state === 'no_pricing_snapshots_recorded') &&
    Array.isArray(x.items) && x.items.every((item: unknown) =>
      record(item) && typeof item.cost_snapshot_id === 'string' &&
      /^[0-9a-f-]{36}$/i.test(item.cost_snapshot_id) &&
      typeof item.sku === 'string' && typeof item.shipment_ref === 'string' &&
      (item.source_evidence_status === 'provisional' || item.source_evidence_status === 'reconciled') &&
      typeof item.source_cost_as_of === 'string') &&
    typeof x.has_more === 'boolean' &&
    x.accounting_reconciled === false && x.price_changes_allowed === false;
}
function validReview(x: unknown, id: string): x is PriceReview {
  return record(x) && x.cost_snapshot_id === id && typeof x.currency === 'string' &&
    (x.recorded_cost_per_case === null || typeof x.recorded_cost_per_case === 'string') &&
    typeof x.source_documentation_complete === 'boolean' &&
    Array.isArray(x.issues_requiring_review) &&
    x.issues_requiring_review.every((issue: unknown) => typeof issue === 'string') &&
    x.accounting_reconciled === false && x.gross_margin_calculated === false &&
    x.price_publication_verified === false && x.external_writes_executed === false;
}
const reasonLabels: Record<string, string> = {
  no_cost_lines: 'Missing cost lines',
  cost_snapshot_provisional: 'Provisional snapshot',
  unverified_cost_lines: 'Expense lines unverified',
  missing_inbound_cost_categories: 'Missing inbound costs',
  missing_warehouse_cost_categories: 'Missing warehouse costs',
  no_linked_price_book: 'No linked price book',
  no_recorded_approved_price: 'No recorded approved price',
};

/** Source-only evidence display. Does not approve, speak or submit external writes. */
export function LolaPricingEvidence({ enabled }: { enabled: boolean }) {
  const [items, setItems] = useState<CandidateList | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [unavailable, setUnavailable] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [review, setReview] = useState<PriceReview | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewUnavailable, setReviewUnavailable] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setLoading(true); setUnavailable(false);
    void api.get<unknown>('/api/lola/chief-of-staff/pricing/snapshots?limit=3')
      .then((result) => {
        if (cancelled) return;
        if (validCandidates(result)) setItems(result);
        else { setItems(null); setUnavailable(true); }
      }).catch(() => { if (!cancelled) { setItems(null); setUnavailable(true); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [enabled]);

  useEffect(() => {
    if (!enabled || !selected) return;
    let cancelled = false;
    setReview(null); setReviewUnavailable(false); setReviewLoading(true);
    void api.get<unknown>('/api/lola/chief-of-staff/pricing/snapshots/' + selected)
      .then((result) => {
        if (cancelled) return;
        if (validReview(result, selected)) setReview(result);
        else setReviewUnavailable(true);
      }).catch(() => { if (!cancelled) setReviewUnavailable(true); })
      .finally(() => { if (!cancelled) setReviewLoading(false); });
    return () => { cancelled = true; };
  }, [enabled, selected]);

  if (!enabled) return null;
  return (
    <section className="lola-pricing" aria-labelledby="lola-pricing-title">
      <p className="lola-home__section-kicker">CHIEF OF STAFF · PRICING</p>
      <h3 id="lola-pricing-title">Pricing evidence to review</h3>
      {loading ? <p role="status">Checking pricing evidence…</p> :
        unavailable || !items ? <p role="status">Pricing evidence is unavailable. No conclusion was produced.</p> :
        items.items.length === 0 ? <p role="status">No cost snapshots were recorded. This does not establish zero costs or prices.</p> :
        <div className="lola-pricing__cards">
          {items.items.map((candidate) => (
            <div className="lola-pricing__card" key={candidate.cost_snapshot_id}>
              <strong>{candidate.sku}</strong>
              <p>Shipment {candidate.shipment_ref} · {candidate.source_cost_as_of}</p>
              <p>{candidate.source_evidence_status === 'reconciled' ? 'Recorded as reconciled' : 'Provisional source'}</p>
              <button type="button"
                aria-expanded={selected === candidate.cost_snapshot_id}
                onClick={() => setSelected((id) => id === candidate.cost_snapshot_id ? null : candidate.cost_snapshot_id)}
              >{selected === candidate.cost_snapshot_id ? 'Close evidence' : 'Inspect evidence'}</button>
              {selected === candidate.cost_snapshot_id && (
                <div className="lola-pricing__detail" aria-live="polite">
                  {reviewLoading ? <p>Loading details…</p> :
                   reviewUnavailable || !review ? <p>Evidence unavailable or inconsistent.</p> :
                   <>
                     <p>Recorded cost per case: {review.recorded_cost_per_case === null ? 'Unavailable' : review.currency + ' ' + review.recorded_cost_per_case}</p>
                     <p>{review.source_documentation_complete ? 'Documentation complete; independent verification pending.' : 'Cost documentation incomplete.'}</p>
                     {review.issues_requiring_review.length > 0 &&
                       <ul>{review.issues_requiring_review.map((reason) => <li key={reason}>{reasonLabels[reason] || 'Further evidence review required'}</li>)}</ul>}
                     <p>No verified gross margin, QuickBooks reconciliation, or published price. No actions executed.</p>
                   </>}
                </div>
              )}
            </div>
          ))}
        </div>
      }
      {items?.has_more && <p>Only the latest three cost snapshots are shown.</p>}
      <p className="lola-pricing__disclaimer">Read-only snapshot evidence; not a live accounting certification or pricing approval.</p>
    </section>
  );
}
