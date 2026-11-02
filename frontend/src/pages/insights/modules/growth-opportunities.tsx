import { useState } from 'react';
import type { InsightMeta } from '../registry';
import {
  AdvisoryNotice, ErrorState, InsightShell, LoadingState, useInsight, type AdvisoryFields,
} from '../ui';

export const meta: InsightMeta = {
  slug: 'growth-opportunities',
  title: 'Growth opportunities',
  description: 'Products growing, slipping, selling below margin or out of stock while customers still order.',
  group: 'Growth',
  // Backend permission run_reports admits owner, manager and back office;
  // each signal is further limited to the roles allowed to see it.
  roles: ['owner', 'manager', 'back_office'],
};

type Section = 'red_alerts' | 'yellow_alerts' | 'opportunities' | 'scoreboard';

interface Signal {
  signal_code: string;
  department: string;
  entity_ref: string;
  product_name: string;
  sku: string;
  status: 'needs_review' | 'source_unavailable';
  display_metric: number | null;
  next_action_code: string;
  evidence_quality: 'verified' | 'unavailable';
}

interface GrowthResponse extends AdvisoryFields {
  weeks: number;
  current_period: { start: string; end: string };
  thresholds: { change_pct: number; min_margin_pct: number };
  sections: Record<Section, Signal[]>;
  signals_total: number;
  rows_skipped: number;
}

const SECTIONS: { key: Section; title: string; tone: string }[] = [
  { key: 'red_alerts', title: 'Needs attention now', tone: 'border-red-200 bg-red-50 text-red-700' },
  { key: 'yellow_alerts', title: 'Keep an eye on', tone: 'border-amber-200 bg-amber-50 text-amber-700' },
  { key: 'opportunities', title: 'Opportunities', tone: 'border-sky-200 bg-sky-50 text-sky-700' },
  { key: 'scoreboard', title: 'Going well', tone: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
];

const SIGNALS: Record<string, { label: string; metric: (value: number) => string }> = {
  brand_growth: { label: 'Sales are growing', metric: (v) => `+${v.toFixed(1)}% cases vs the period before` },
  brand_decline: { label: 'Sales are slipping', metric: (v) => `${v.toFixed(1)}% cases vs the period before` },
  margin_breach: { label: 'Selling below your margin target', metric: (v) => `${v.toFixed(1)}% gross margin` },
  portfolio_stockout_with_demand: {
    label: 'Out of stock while customers still order', metric: (v) => `${v.toLocaleString()} cases ordered this period`,
  },
};

const NEXT_STEPS: Record<string, string> = {
  review_growth_evidence: 'Look at what is driving the growth and whether to back it.',
  review_brand_vitality: 'Review the product scorecard to find out why sales dropped.',
  review_margin_waterfall: 'Walk the price waterfall to see where the margin goes.',
  review_supply_availability: 'Check supply and incoming orders for this product.',
  verify_source_evidence: 'Double-check the figures in your system before acting; this source is not fully verified.',
};

const WEEK_OPTIONS = [4, 8, 13] as const;

function SignalCard({ signal }: { signal: Signal }) {
  const definition = SIGNALS[signal.signal_code];
  return (
    <li className="rounded-xl border border-slate-200 bg-white p-3 space-y-1">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm font-semibold text-slate-800">{signal.product_name || signal.entity_ref}</span>
        <span className={`text-xs font-medium px-2 py-0.5 rounded-lg ${
          signal.evidence_quality === 'verified' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>
          {signal.evidence_quality === 'verified' ? 'From QuickBooks records' : 'Source needs checking'}
        </span>
      </div>
      <div className="text-sm text-slate-700">{definition?.label ?? signal.signal_code.replace(/_/g, ' ')}</div>
      {signal.display_metric != null && definition && (
        <div className="text-xs text-slate-500">{definition.metric(signal.display_metric)}</div>
      )}
      <div className="text-xs text-slate-500">
        Next step: {NEXT_STEPS[signal.next_action_code] ?? signal.next_action_code.replace(/_/g, ' ')}
      </div>
    </li>
  );
}

export default function GrowthOpportunities() {
  const [weeks, setWeeks] = useState<number>(4);
  const { data, error, loading, reload } = useInsight<GrowthResponse>(
    `/api/insights/growth-opportunities?weeks=${weeks}`,
  );

  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source="connected_data" />
      <label className="text-sm text-slate-600">
        Compare the last{' '}
        <select value={weeks} onChange={(e) => setWeeks(Number(e.target.value))}
          className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-sm">
          {WEEK_OPTIONS.map((option) => <option key={option} value={option}>{option} weeks</option>)}
        </select>{' '}
        with the period before
      </label>
      {loading && <LoadingState />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && !loading && (
        <>
          <p className="text-xs text-slate-500">
            Flags products whose cases sold moved by {data.thresholds.change_pct}% or more, or whose gross margin is
            under {data.thresholds.min_margin_pct}%. You only see the items your role is allowed to review.
          </p>
          {data.signals_total === 0 && (
            <p className="text-sm text-slate-500">Nothing to flag for this period.</p>
          )}
          {SECTIONS.filter(({ key }) => (data.sections[key] ?? []).length > 0).map(({ key, title, tone }) => (
            <section key={key} aria-label={title} className="space-y-2">
              <h2 className={`inline-block rounded-lg border px-2 py-0.5 text-xs font-semibold ${tone}`}>
                {title} ({data.sections[key].length})
              </h2>
              <ul className="grid gap-2 sm:grid-cols-2">
                {data.sections[key].map((signal) => (
                  <SignalCard key={`${signal.signal_code}-${signal.entity_ref}`} signal={signal} />
                ))}
              </ul>
            </section>
          ))}
          {data.rows_skipped > 0 && (
            <p className="text-xs text-amber-700">
              {data.rows_skipped} record(s) could not be read and were left out.
            </p>
          )}
        </>
      )}
    </InsightShell>
  );
}
