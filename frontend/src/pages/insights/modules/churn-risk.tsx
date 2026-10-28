/** Customers at risk of leaving, from GET /api/insights/churn-risk (view_customers). */
import type { InsightMeta } from '../registry';
import {
  AdvisoryNotice, ErrorState, InsightShell, LoadingState, useInsight, type AdvisoryFields,
} from '../ui';

export const meta: InsightMeta = {
  slug: 'churn-risk',
  title: 'Customers at risk of leaving',
  description: 'Customers who are ordering less or have stopped, why it looks that way, and a suggested next step.',
  group: 'Customers',
  // view_customers: owner and manager (view_all) and back office. Not sales
  // reps: their account assignments are not verified by the server yet.
  roles: ['owner', 'manager', 'back_office'],
};

type Risk = 'high' | 'medium' | 'low' | 'unknown' | 'not_applicable';

export interface ChurnCustomer {
  customer_id: string;
  name: string;
  risk: Risk;
  score: number | null;
  why: string;
  next_step: string;
  last_order_date: string | null;
  orders_last_12_months: number;
  history_complete: boolean;
}

export interface ChurnRiskResponse extends AdvisoryFields {
  as_of: string;
  customers_assessed: number;
  counts: Record<Risk, number>;
  at_risk: ChurnCustomer[];
  insufficient_data: ChurnCustomer[];
  truncated: boolean;
  rows_skipped: number;
  rows_unattributed: number;
  customers_skipped: number;
  method: string;
}

const RISK_CFG: Record<Risk, { label: string; badge: string }> = {
  high:           { label: 'High risk',          badge: 'bg-red-50 text-red-700 border-red-200' },
  medium:         { label: 'Slowing down',       badge: 'bg-amber-50 text-amber-700 border-amber-200' },
  low:            { label: 'Ordering normally',  badge: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  unknown:        { label: 'Not enough history', badge: 'bg-slate-50 text-slate-600 border-slate-200' },
  not_applicable: { label: 'No orders yet',      badge: 'bg-slate-50 text-slate-600 border-slate-200' },
};

const SUMMARY_ORDER: Risk[] = ['high', 'medium', 'low', 'unknown', 'not_applicable'];

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function RiskBadge({ risk }: { risk: Risk }) {
  const cfg = RISK_CFG[risk];
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-xs font-medium ${cfg.badge}`}>{cfg.label}</span>
  );
}

function ChurnView({ data }: { data: ChurnRiskResponse }) {
  return (
    <div className="space-y-5">
      <div className="grid gap-3 grid-cols-2 sm:grid-cols-5">
        {SUMMARY_ORDER.map((risk) => (
          <div key={risk} className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="text-2xl font-bold text-slate-900">{data.counts[risk] ?? 0}</div>
            <div className="text-xs text-slate-500 mt-1">{RISK_CFG[risk].label}</div>
          </div>
        ))}
      </div>

      {(data.rows_skipped > 0 || data.rows_unattributed > 0) && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 space-y-1">
          {data.rows_skipped > 0 && (
            <p>{data.rows_skipped} order(s) could not be read and were left out. Customers they belong to are listed under “Not enough history”.</p>
          )}
          {data.rows_unattributed > 0 && (
            <p>{data.rows_unattributed} order(s) had no customer attached, so some customers’ results may be affected.</p>
          )}
        </div>
      )}

      <section aria-label="Customers to check on" className="rounded-xl border border-slate-200 bg-white overflow-hidden">
        <h2 className="px-5 py-3 text-sm font-semibold text-slate-800 border-b border-slate-100">Customers to check on</h2>
        {data.at_risk.length === 0 ? (
          <p className="px-5 py-4 text-sm text-slate-500">No customers look at risk right now.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-slate-100">
                <tr>
                  {['Customer', 'Risk', 'Why', 'Last order', 'Suggested next step'].map((h) => (
                    <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {data.at_risk.map((c) => (
                  <tr key={c.customer_id} className="align-top">
                    <td className="px-5 py-3 text-sm font-medium text-slate-800">{c.name}</td>
                    <td className="px-5 py-3"><RiskBadge risk={c.risk} /></td>
                    <td className="px-5 py-3 text-sm text-slate-600">{c.why}</td>
                    <td className="px-5 py-3 text-sm text-slate-600 whitespace-nowrap">{fmtDate(c.last_order_date)}</td>
                    <td className="px-5 py-3 text-sm text-slate-700">{c.next_step}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {data.insufficient_data.length > 0 && (
        <section aria-label="Not enough history" className="rounded-xl border border-slate-200 bg-white p-5 space-y-2">
          <h2 className="text-sm font-semibold text-slate-800">Not enough history to judge</h2>
          <ul className="divide-y divide-slate-50">
            {data.insufficient_data.map((c) => (
              <li key={c.customer_id} className="py-2 text-sm">
                <span className="font-medium text-slate-800">{c.name}</span>
                <span className="text-slate-500"> — {c.why}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {data.truncated && (
        <p className="text-xs text-slate-500">Only the first customers in each list are shown.</p>
      )}
      <p className="text-xs text-slate-400">
        How this works: {data.method} {data.customers_assessed} active customers checked.
      </p>
    </div>
  );
}

export default function ChurnRisk() {
  const { data, error, loading, reload } = useInsight<ChurnRiskResponse>('/api/insights/churn-risk');
  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source={data?.source} />
      {loading && <LoadingState />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && !loading && <ChurnView data={data} />}
    </InsightShell>
  );
}
