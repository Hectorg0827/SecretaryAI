import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../lib/api';
import './chief-of-staff-inbox.css';

type LolaReviewCard = {
  id: string;
  severity: string;
  title: string;
  summary: string;
  evidence_status: string;
  estimated_financial_exposure_usd: number | null;
  financial_exposure_kind: string;
};
type Briefing = {
  state: 'needs_attention' | 'no_visible_shipments' | 'no_visible_logistics_exceptions';
  visible_alert_count: number;
  cards_truncated: boolean;
  cards: LolaReviewCard[];
};

/** Passive source-backed review only: never speaks, sends, or approves. */
export function ChiefOfStaffInbox({ enabled }: { enabled: boolean }) {
  const [data, setData] = useState<Briefing | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setLoading(true);
    setUnavailable(false);
    void api.get<Briefing>('/api/lola/chief-of-staff/briefing?limit=3').then((result) => {
      if (!cancelled) setData(result);
    }).catch(() => {
      if (!cancelled) { setData(null); setUnavailable(true); }
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [enabled]);

  if (!enabled) return null;
  return (
    <section className="lola-chief" aria-labelledby="lola-chief-heading">
      <header className="lola-chief__heading">
        <div>
          <p className="lola-home__section-kicker">CHIEF OF STAFF · LOGISTICS</p>
          <h3 id="lola-chief-heading">Needs your attention</h3>
        </div>
        <Link to="/logistics">Open logistics</Link>
      </header>
      {loading ? <p className="lola-chief__note" role="status">Checking authorized logistics evidence…</p> :
        unavailable || !data ? <p className="lola-chief__note" role="status">Briefing source unavailable. No all-clear can be inferred.</p> : (
          <>
            {data.state === 'no_visible_shipments' ?
              <p className="lola-chief__note">No shipments are visible in the logistics ledger. Coverage is incomplete.</p> :
             data.state === 'no_visible_logistics_exceptions' ?
              <p className="lola-chief__note">No exceptions found in visible logistics records. Other systems were not assessed.</p> : (
                <>
                  <p className="lola-chief__note">{data.visible_alert_count} logistics exception{data.visible_alert_count === 1 ? '' : 's'} detected. No actions executed.</p>
                  <div className="lola-chief__cards">
                    {data.cards.map((card) => (
                      <Link className="lola-chief__card" key={card.id} to="/logistics">
                        <span className="lola-chief__severity">{card.severity.toUpperCase()} · {card.evidence_status === 'unverified_report_requires_confirmation' ? 'Unverified report' : 'Ledger-derived'}</span>
                        <strong>{card.title}</strong>
                        <span>{card.summary}</span>
                        {card.estimated_financial_exposure_usd != null && card.financial_exposure_kind === 'model_estimate_not_ledger_reconciled' ?
                          <em>Estimated exposure: USD {card.estimated_financial_exposure_usd.toLocaleString()}</em> : null}
                        <span className="lola-chief__view">Inspect in Logistics</span>
                      </Link>
                    ))}
                  </div>
                  {data.cards_truncated && <p className="lola-chief__note">More alerts are available in Logistics.</p>}
                </>
              )}
            <p className="lola-chief__footnote">Logistics-only monitoring. QuickBooks and pricing are not reconciled here. No notifications, autonomous voice or financial updates were performed.</p>
          </>
        )}
    </section>
  );
}
