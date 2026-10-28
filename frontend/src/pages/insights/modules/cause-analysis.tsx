import type { InsightMeta } from '../registry';
import {
  AdvisoryNotice, ErrorState, InsightShell, LoadingState, useInsight, type AdvisoryFields,
} from '../ui';

// Backend: GET /api/insights/cause-analysis requires view_customers.
export const meta: InsightMeta = {
  slug: 'cause-analysis',
  title: 'Why sales or stock moved',
  description: 'Customers buying less and products running short, with the most likely causes ranked.',
  group: 'Supply chain',
  roles: ['owner', 'manager', 'back_office'],
};

interface Cause {
  root_cause: string;
  probability: number;
  evidence: string[];
  intervention: string;
}

interface Analysis {
  anomaly_description: string;
  primary_cause: Cause;
  alternative_causes: Cause[];
  customer_segment: string | null;
  segment_reasoning: string;
  data_quality_warning: string;
}

interface CustomerDrop {
  customer_id: string;
  customer_name: string;
  trigger: string;
  revenue_last_90_days: number;
  revenue_prior_90_days: number;
  analysis: Analysis;
}

interface StockRisk {
  item_id: string | null;
  product_name: string;
  sku: string | null;
  days_of_stock_left: number;
  avg_daily_sales: number;
  typical_supplier_days: number | null;
  late_purchase_orders: number;
  analysis: Analysis;
}

interface CauseResponse extends AdvisoryFields {
  customer_drops: CustomerDrop[];
  stock_risks: StockRisk[];
  summary: {
    customers_reviewed: number;
    customers_flagged: number;
    customers_with_too_little_history: number;
    items_with_recent_sales: number;
    items_flagged: number;
    rows_skipped: number;
  };
  evidence_gaps: string[];
}

const CAUSE_LABEL: Record<string, string> = {
  demand_shift: "Customer's needs changed",
  supply_disruption: 'Supplier delay or shortage',
  pricing_pressure: 'Price increase or competitor pricing',
  seasonal_pattern: 'Normal seasonal slowdown',
  quality_event: 'Product quality problem',
  operational_failure: 'Our own picking or shipping error',
  external_macro: 'Outside event (economy, weather, rules)',
  unknown: 'Cause unclear',
};

const SEGMENT_LABEL: Record<string, string> = {
  natural_recoverer: 'Likely to come back on their own',
  persuadable: 'Worth a personal follow-up',
  lost_cause: 'Unlikely to return; keep an eye on it',
};

function causeLabel(code: string) {
  return CAUSE_LABEL[code] ?? code.replace(/_/g, ' ');
}

function money(value: number) {
  return value.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
}

function CauseCard({ title, subtitle, facts, analysis }: {
  title: string; subtitle: string; facts: string[]; analysis: Analysis;
}) {
  const primary = analysis.primary_cause;
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-4 space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
        <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>
        {facts.length > 0 && <p className="text-xs text-slate-400 mt-0.5">{facts.join(' · ')}</p>}
      </div>
      <div className="rounded-lg bg-slate-50 px-3 py-2">
        <div className="text-xs text-slate-500">Most likely cause</div>
        <div className="text-sm font-semibold text-slate-800">
          {causeLabel(primary.root_cause)}
          <span className="ml-2 text-xs font-normal text-slate-500">
            strength {Math.round(primary.probability * 100)}/100 (rule of thumb, not a measured probability)
          </span>
        </div>
        {primary.evidence.length > 0 && (
          <ul className="list-disc pl-5 mt-1 text-xs text-slate-600">
            {primary.evidence.map((item) => <li key={item}>{item}</li>)}
          </ul>
        )}
      </div>
      <p className="text-sm text-slate-700">
        <span className="font-medium">Possible next step: </span>{primary.intervention}
      </p>
      {analysis.customer_segment && (
        <p className="text-xs text-slate-500">
          {SEGMENT_LABEL[analysis.customer_segment] ?? analysis.customer_segment}. {analysis.segment_reasoning}
        </p>
      )}
      {analysis.alternative_causes.length > 0 && (
        <p className="text-xs text-slate-500">
          Other possible causes: {analysis.alternative_causes.map((c) => causeLabel(c.root_cause)).join(', ')}
        </p>
      )}
      {analysis.data_quality_warning && <p className="text-xs text-amber-700">{analysis.data_quality_warning}</p>}
    </article>
  );
}

export default function CauseAnalysisInsight() {
  const { data, error, loading, reload } = useInsight<CauseResponse>('/api/insights/cause-analysis');

  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source={data?.source ?? 'connected_data'} />
      {loading && <LoadingState />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && !loading && (
        <>
          <p className="text-xs text-slate-500">
            Causes are ranked by simple rules from the last 12 months of invoices, your stock and purchase orders.
            They are starting points to check, not proven explanations.
            {data.summary.rows_skipped > 0 && ` ${data.summary.rows_skipped} unreadable records were skipped.`}
          </p>
          {data.evidence_gaps.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-amber-700">
              {data.evidence_gaps.map((gap) => <li key={gap}>{gap}</li>)}
            </ul>
          )}

          <section aria-label="Customers buying less" className="space-y-3">
            <h2 className="text-sm font-semibold text-slate-800">
              Customers buying less ({data.summary.customers_flagged} of {data.summary.customers_reviewed})
            </h2>
            {data.customer_drops.length === 0 ? (
              <p className="text-sm text-slate-500">No regular customer has gone quiet or cut spending sharply.</p>
            ) : (
              <div className="grid gap-3 lg:grid-cols-2">
                {data.customer_drops.map((c) => (
                  <CauseCard
                    key={c.customer_id}
                    title={c.customer_name}
                    subtitle={c.trigger}
                    facts={[
                      `Last 90 days ${money(c.revenue_last_90_days)}`,
                      `90 days before ${money(c.revenue_prior_90_days)}`,
                    ]}
                    analysis={c.analysis}
                  />
                ))}
              </div>
            )}
            {data.summary.customers_with_too_little_history > 0 && (
              <p className="text-xs text-slate-400">
                {data.summary.customers_with_too_little_history} customers have too few orders to judge.
              </p>
            )}
          </section>

          <section aria-label="Products running short" className="space-y-3">
            <h2 className="text-sm font-semibold text-slate-800">Products running short ({data.summary.items_flagged})</h2>
            {data.stock_risks.length === 0 ? (
              <p className="text-sm text-slate-500">No product with recent sales is close to running out.</p>
            ) : (
              <div className="grid gap-3 lg:grid-cols-2">
                {data.stock_risks.map((s) => (
                  <CauseCard
                    key={`${s.item_id ?? ''}-${s.sku ?? s.product_name}`}
                    title={s.product_name}
                    subtitle={`About ${s.days_of_stock_left} days of stock left at ${s.avg_daily_sales} sold per day`}
                    facts={[
                      s.typical_supplier_days != null
                        ? `Supplier usually takes ${s.typical_supplier_days} days`
                        : 'Supplier delivery time not yet known',
                      ...(s.late_purchase_orders > 0 ? [`${s.late_purchase_orders} purchase order(s) running late`] : []),
                    ]}
                    analysis={s.analysis}
                  />
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </InsightShell>
  );
}
