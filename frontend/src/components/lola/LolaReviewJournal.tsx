import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../lib/api';
import './lola-review-journal.css';

type ReviewEvent = {
  id: string;
  source_alert_id: string;
  shipment_ref: string;
  alert_code: string;
  severity: string;
  evidence_quality: string;
  source_revision: string;
  review_state: 'pending' | 'acknowledged';
  first_seen_at: string;
};
type JournalResponse = {
  scope: 'recorded_logistics_reviews_only';
  state: 'recorded_reviews' | 'no_recorded_review_events';
  items: ReviewEvent[];
  next_offset: number | null;
  review_does_not_approve_external_actions: true;
  external_writes_executed: false;
};
type CaptureResponse = {
  state: 'capture_completed' | 'no_scoped_alerts_detected';
  candidate_records: number;
  external_writes_executed: false;
  approval_created: false;
};

function isObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
function verifiedList(value: unknown): value is JournalResponse {
  return isObject(value) && value.scope === 'recorded_logistics_reviews_only' &&
    (value.state === 'recorded_reviews' || value.state === 'no_recorded_review_events') &&
    value.review_does_not_approve_external_actions === true &&
    value.external_writes_executed === false &&
    Array.isArray(value.items) &&
    value.items.every((item: unknown) => isObject(item) &&
      typeof item.id === 'string' && typeof item.source_alert_id === 'string' &&
      typeof item.shipment_ref === 'string' && typeof item.alert_code === 'string' &&
      typeof item.severity === 'string' && typeof item.first_seen_at === 'string' &&
      (item.review_state === 'pending' || item.review_state === 'acknowledged') &&
      typeof item.source_revision === 'string' &&
      /^[0-9a-f]{64}$/.test(item.source_revision));
}
function verifiedCapture(value: unknown): value is CaptureResponse {
  return isObject(value) &&
    (value.state === 'capture_completed' || value.state === 'no_scoped_alerts_detected') &&
    typeof value.candidate_records === 'number' &&
    value.external_writes_executed === false && value.approval_created === false;
}
function verifiedAck(value: unknown, id: string): boolean {
  return isObject(value) && value.review_id === id &&
    value.review_state === 'acknowledged' && value.external_writes_executed === false;
}

/** Manual, internal-only review history. No financial or provider action buttons. */
export function LolaReviewJournal({ enabled }: { enabled: boolean }) {
  const [journal, setJournal] = useState<JournalResponse | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [unavailable, setUnavailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reloadIndex, setReloadIndex] = useState(0);
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setLoading(true);
    setUnavailable(false);
    void api.get<unknown>('/api/lola/chief-of-staff/reviews?limit=5')
      .then((data) => {
        if (cancelled) return;
        if (verifiedList(data)) setJournal(data);
        else { setJournal(null); setUnavailable(true); }
      }).catch(() => {
        if (!cancelled) { setJournal(null); setUnavailable(true); }
      }).finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [enabled, reloadIndex]);

  const capture = async () => {
    if (busy || loading) return;
    setBusy(true);
    setMessage('');
    try {
      const result = await api.post<unknown>('/api/lola/chief-of-staff/reviews/capture', {});
      if (!verifiedCapture(result)) throw new Error('Unverifiable capture result');
      setMessage(result.state === 'capture_completed'
        ? 'Logistics exceptions recorded or deduplicated. No actions performed.'
        : 'No scoped logistics exceptions detected. This is not a full business all-clear.');
      setReloadIndex((value) => value + 1);
    } catch {
      setMessage('Review capture unavailable. No successful persistence is assumed.');
    } finally {
      setBusy(false);
    }
  };

  const acknowledge = async (item: ReviewEvent) => {
    if (busy || loading) return;
    setBusy(true);
    setMessage('');
    try {
      const result = await api.post<unknown>(
        '/api/lola/chief-of-staff/reviews/' + item.id + '/acknowledge',
        { expected_source_revision: item.source_revision },
      );
      if (!verifiedAck(result, item.id)) throw new Error('Unverifiable acknowledgement');
      setMessage('Observation acknowledged; no business action was approved or executed.');
      setReloadIndex((value) => value + 1);
    } catch {
      setMessage('Review changed or unavailable. Reload the journal before acknowledging.');
    } finally {
      setBusy(false);
    }
  };

  if (!enabled) return null;
  return (
    <section className="lola-journal" aria-labelledby="lola-journal-title">
      <div className="lola-journal__heading">
        <div>
          <p className="lola-home__section-kicker">CHIEF OF STAFF · REVIEW HISTORY</p>
          <h3 id="lola-journal-title">Recorded decisions to review</h3>
        </div>
        <button disabled={busy || loading} type="button" onClick={() => void capture()}>
          {busy ? 'Processing…' : 'Record current exceptions'}
        </button>
      </div>
      <p className="lola-journal__explanation">
        Manual, source-referenced logistics review log. Recording or acknowledging an
        observation never authorizes a shipment, email, price, or payment action.
      </p>
      {message && <p role="status" className="lola-journal__note">{message}</p>}
      {loading ? <p role="status">Loading recorded review evidence…</p> :
        unavailable || !journal ?
          <p role="status">Review history unavailable or not installed in this environment. This is not an all-clear.</p> :
          journal.items.length === 0 ?
            <p role="status">No review events have been recorded. This does not mean there are no operational risks.</p> :
            <div className="lola-journal__items">
              {journal.items.map((item) => (
                <article key={item.id} className="lola-journal__item">
                  <span className="lola-journal__meta">
                    {item.severity.toUpperCase()} · {item.review_state === 'pending' ? 'Pending review' : 'Acknowledged'}
                  </span>
                  <strong>{item.alert_code.replace(/_/g, ' ')}</strong>
                  <span>Shipment {item.shipment_ref}</span>
                  <span className="lola-journal__source">
                    {item.evidence_quality === 'unverified_report_requires_confirmation'
                      ? 'Unverified report — confirmation required'
                      : 'Derived from the logistics event ledger'}
                  </span>
                  {item.review_state === 'pending' && (
                    <button disabled={busy} type="button" onClick={() => void acknowledge(item)}>
                      Acknowledge observation
                    </button>
                  )}
                </article>
              ))}
            </div>
      }
      {journal?.next_offset !== null && journal?.next_offset !== undefined &&
        <p className="lola-journal__note">More historical reviews exist beyond this page.</p>}
      <p className="lola-journal__footer">
        <Link to="/logistics">Inspect source logistics records</Link>
        <span> · No automatic monitoring or external writes enabled</span>
      </p>
    </section>
  );
}
