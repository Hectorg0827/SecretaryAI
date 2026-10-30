import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type WeeklyReportResponseV1, type WeeklyReportRunV1 } from '../../lib/api';
import { verifiedWeeklyReport } from '../../lib/sourceEvidence';

type WeeklyState = { status: 'loading' | 'ready' | 'unavailable'; data: WeeklyReportResponseV1 | null };

const FAILURE_TEXT: Record<NonNullable<WeeklyReportRunV1['reason']>, string> = {
  source_unavailable: 'your business data could not be read',
  generation_failed: 'the report could not be written',
  storage_failed: 'the report could not be saved',
};

const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

/** A YYYY-MM-DD calendar date shown as that same day in every time zone. */
function day(value: string): string {
  const [y, m, d] = value.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function week(start: string, end: string): string {
  return `${day(start)} – ${day(end)}`;
}

/** Owner/manager weekly leadership report, written each Sunday morning. */
export function WeeklyReport() {
  const [state, setState] = useState<WeeklyState>({ status: 'loading', data: null });
  const requestEpoch = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++requestEpoch.current;
    setState({ status: 'loading', data: null });
    try {
      const data = verifiedWeeklyReport(await api.briefing.weekly());
      if (!data) throw new Error('Weekly report contract mismatch');
      if (requestId === requestEpoch.current) setState({ status: 'ready', data });
    } catch {
      if (requestId === requestEpoch.current) setState({ status: 'unavailable', data: null });
    }
  }, []);

  useEffect(() => {
    void load();
    const tracker = requestEpoch;
    return () => { tracker.current++; };
  }, [load]);

  const report = state.data?.report ?? null;
  const run = state.data?.last_run ?? null;
  // A failed run only matters if it is newer than the report on screen.
  const failedRun = run && run.status === 'failed' && (!report || run.period_end > report.period_end) ? run : null;
  const f = report?.figures;

  return <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm space-y-3" aria-labelledby="weekly-report-heading">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div>
        <h2 id="weekly-report-heading" className="text-lg font-bold text-slate-900">Weekly report</h2>
        <p className="text-xs text-slate-600">Prepared every Sunday morning from your sales, account and stock data.</p>
      </div>
      <button type="button" onClick={() => void load()} disabled={state.status === 'loading'}
        className="text-xs rounded-full border border-slate-300 px-3 py-1 text-slate-700 hover:bg-slate-100 disabled:opacity-50">
        {state.status === 'loading' ? 'Loading…' : 'Refresh'}
      </button>
    </div>

    {state.status === 'loading' && <p className="text-sm text-slate-600" role="status">Loading your weekly report…</p>}

    {state.status === 'unavailable' && <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
      We couldn’t load your weekly report right now. Please try again in a few minutes.
    </div>}

    {failedRun && <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
      The report for {week(failedRun.period_start, failedRun.period_end)} could not be prepared because{' '}
      {FAILURE_TEXT[failedRun.reason ?? 'source_unavailable']}. No report was made from incomplete data.
      {report && <span className="block mt-1">Below is your most recent report.</span>}
    </div>}

    {state.status === 'ready' && !report && !failedRun && <p className="text-sm text-slate-700">
      Your first weekly report will appear here after it is prepared on Sunday morning.
    </p>}

    {report && f && <article className="space-y-2">
      <p className="text-xs font-semibold text-slate-700">
        Week of {week(report.period_start, report.period_end)}
        {report.generated_at && <> · prepared {new Date(report.generated_at).toLocaleString()}</>}
      </p>
      <div className="whitespace-pre-wrap text-sm text-slate-800">{report.report_text}</div>
      <p className="pt-2 border-t border-slate-100 text-[11px] text-slate-600">
        Based on: {money.format(f.sales_this_week)} in sales this week ({f.invoices_this_week} invoices)
        vs {money.format(f.sales_prior_week)} the week before ({f.invoices_prior_week} invoices) ·{' '}
        {f.accounts_at_risk} at-risk and {f.accounts_dormant} dormant of {f.accounts_total} accounts ·{' '}
        {f.inventory_critical} critical and {f.inventory_low} low stock items.
        {' '}Written by AI from these figures; check important numbers before acting.
      </p>
    </article>}
  </section>;
}
