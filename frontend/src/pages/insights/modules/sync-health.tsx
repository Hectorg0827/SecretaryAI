import clsx from 'clsx';
import { AlertTriangle, CheckCircle2, CircleHelp, Clock, XCircle } from 'lucide-react';
import type { InsightMeta } from '../registry';
import {
  AdvisoryNotice, ErrorState, InsightShell, LoadingState, useInsight, type AdvisoryFields,
} from '../ui';

export const meta: InsightMeta = {
  slug: 'sync-health',
  title: 'Sync health',
  description: 'Whether QuickBooks and your other connected systems agree, when they were last checked, and any differences found.',
  group: 'Operations',
  roles: ['owner', 'manager'],
};

type SyncState = 'never_run' | 'in_sync' | 'not_compared' | 'mismatches_found' | 'check_failed' | 'unknown';

interface SyncMismatch {
  item: string;
  severity: 'info' | 'warning' | 'critical';
  message: string;
  difference_pct: number | null;
}

interface SyncCheck {
  check_type: string;
  label: string;
  systems_compared: string[];
  recorded_status: string;
  state: SyncState;
  checked_at: string | null;
  mismatch_count: number;
  mismatches: SyncMismatch[];
}

interface SyncHistoryEntry {
  check_type: string;
  label: string;
  state: SyncState;
  checked_at: string | null;
  mismatch_count: number;
}

/** GET /api/insights/sync-health (backend/app/api/insights/sync_health.py). */
export interface SyncHealthResponse extends AdvisoryFields {
  state: SyncState;
  has_run: boolean;
  last_checked_at: string | null;
  stale: boolean;
  stale_after_hours: number;
  checks: SyncCheck[];
  history: SyncHistoryEntry[];
}

const STATE_CFG: Record<SyncState, {
  badge: string; headline: string; card: string; icon: typeof CheckCircle2; iconClass: string;
}> = {
  never_run: {
    badge: 'Not checked yet', headline: 'No sync check has run yet',
    card: 'border-slate-300 bg-slate-50', icon: CircleHelp, iconClass: 'text-slate-500',
  },
  in_sync: {
    badge: 'In agreement', headline: 'Your connected systems agree',
    card: 'border-emerald-200 bg-emerald-50', icon: CheckCircle2, iconClass: 'text-emerald-600',
  },
  not_compared: {
    badge: 'Not compared', headline: 'Nothing was compared in the last check',
    card: 'border-amber-200 bg-amber-50', icon: AlertTriangle, iconClass: 'text-amber-600',
  },
  mismatches_found: {
    badge: 'Differences found', headline: 'Your connected systems disagree',
    card: 'border-red-200 bg-red-50', icon: XCircle, iconClass: 'text-red-600',
  },
  check_failed: {
    badge: 'Check failed', headline: 'The last check could not finish',
    card: 'border-red-200 bg-red-50', icon: XCircle, iconClass: 'text-red-600',
  },
  unknown: {
    badge: 'Unclear result', headline: 'The last result could not be interpreted',
    card: 'border-amber-200 bg-amber-50', icon: CircleHelp, iconClass: 'text-amber-600',
  },
};

const BADGE_CLASS: Record<SyncState, string> = {
  never_run: 'bg-slate-100 text-slate-700',
  in_sync: 'bg-emerald-100 text-emerald-700',
  not_compared: 'bg-amber-100 text-amber-700',
  mismatches_found: 'bg-red-100 text-red-700',
  check_failed: 'bg-red-100 text-red-700',
  unknown: 'bg-amber-100 text-amber-700',
};

const SEVERITY_CLASS: Record<SyncMismatch['severity'], string> = {
  info: 'bg-slate-100 text-slate-700',
  warning: 'bg-amber-100 text-amber-700',
  critical: 'bg-red-100 text-red-700',
};

const SEVERITY_LABEL: Record<SyncMismatch['severity'], string> = {
  info: 'Minor',
  warning: 'Check soon',
  critical: 'Large gap',
};

function stateCfg(state: SyncState) {
  return STATE_CFG[state] ?? STATE_CFG.unknown;
}

function formatWhen(iso: string | null): string {
  if (!iso) return 'Time not recorded';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return 'Time not recorded';
  return parsed.toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

function headlineDetail(data: SyncHealthResponse): string {
  const mismatchTotal = data.checks.reduce((sum, check) => sum + check.mismatch_count, 0);
  switch (data.state) {
    case 'never_run':
      return 'SecretaryAI compares QuickBooks with your other connected systems on a schedule. '
        + 'No check has been recorded for your company yet, so we cannot tell whether your systems agree.';
    case 'in_sync':
      return 'The most recent check compared your systems and found no differences.';
    case 'not_compared':
      return 'The last check could only read QuickBooks. No second system (such as a warehouse system) '
        + 'was available to compare against, so agreement is not confirmed.';
    case 'mismatches_found':
      return mismatchTotal > 0
        ? `${mismatchTotal} difference${mismatchTotal === 1 ? '' : 's'} found in the latest check. Review them below.`
        : 'The latest check reported differences, but the details were not recorded.';
    case 'check_failed':
      return 'The check could not read data from QuickBooks. Check your QuickBooks connection in Settings.';
    default:
      return 'The last check recorded a result we do not recognise, so we cannot say whether your systems agree.';
  }
}

function StateBadge({ state }: { state: SyncState }) {
  return (
    <span className={clsx('text-xs font-semibold px-2 py-1 rounded-lg whitespace-nowrap', BADGE_CLASS[state] ?? BADGE_CLASS.unknown)}>
      {stateCfg(state).badge}
    </span>
  );
}

function CheckCard({ check }: { check: SyncCheck }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white">
      <div className="flex flex-wrap items-start justify-between gap-2 px-4 py-3 border-b border-slate-100">
        <div>
          <div className="text-sm font-semibold text-slate-900">{check.label}</div>
          <div className="text-xs text-slate-500 mt-0.5">
            Checked {formatWhen(check.checked_at)}
            {check.systems_compared.length > 0 && <> · Compares {check.systems_compared.join(' and ')}</>}
          </div>
        </div>
        <StateBadge state={check.state} />
      </div>
      {check.mismatches.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">
                <th className="px-4 py-2">Item</th>
                <th className="px-4 py-2">What&apos;s different</th>
                <th className="px-4 py-2 text-right">Gap</th>
                <th className="px-4 py-2">How serious</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {check.mismatches.map((m, i) => (
                <tr key={`${m.item}-${i}`}>
                  <td className="px-4 py-2 font-medium text-slate-800">{m.item || '—'}</td>
                  <td className="px-4 py-2 text-slate-600">{m.message || '—'}</td>
                  <td className="px-4 py-2 text-right text-slate-700">
                    {m.difference_pct != null ? `${m.difference_pct.toFixed(1)}%` : '—'}
                  </td>
                  <td className="px-4 py-2">
                    <span className={clsx('text-xs font-semibold px-2 py-0.5 rounded-lg', SEVERITY_CLASS[m.severity] ?? SEVERITY_CLASS.warning)}>
                      {SEVERITY_LABEL[m.severity] ?? SEVERITY_LABEL.warning}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="px-4 py-3 text-sm text-slate-500">
          {check.state === 'in_sync'
            ? 'No differences found.'
            : check.state === 'mismatches_found'
              ? 'Differences were reported but no details were recorded.'
              : 'No comparison results to show for this check.'}
        </p>
      )}
    </div>
  );
}

function Headline({ data }: { data: SyncHealthResponse }) {
  const cfg = stateCfg(data.state);
  const Icon = cfg.icon;
  return (
    <div className={clsx('flex gap-3 items-start rounded-xl border px-4 py-4', cfg.card)}>
      <Icon size={22} className={clsx('mt-0.5 flex-shrink-0', cfg.iconClass)} />
      <div className="space-y-1">
        <div className="text-base font-bold text-slate-900">{cfg.headline}</div>
        <p className="text-sm text-slate-700">{headlineDetail(data)}</p>
        <p className="text-xs text-slate-500 flex items-center gap-1">
          <Clock size={12} />
          {data.has_run ? `Last checked ${formatWhen(data.last_checked_at)}` : 'Never checked'}
        </p>
      </div>
    </div>
  );
}

export default function SyncHealthInsight() {
  const { data, error, loading, reload } = useInsight<SyncHealthResponse>('/api/insights/sync-health');

  return (
    <InsightShell title={meta.title} description={meta.description}>
      {loading ? (
        <LoadingState label="Loading sync checks…" />
      ) : error ? (
        <ErrorState message={`Sync check history is unavailable: ${error}`} onRetry={reload} />
      ) : data ? (
        <>
          <Headline data={data} />

          {data.has_run && data.stale && (
            <div role="note" className="flex gap-2 items-start rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
              <span>
                The last check was more than {data.stale_after_hours} hours ago. Automatic checks may not be
                running, so these results could be out of date.
              </span>
            </div>
          )}

          {data.checks.length > 0 && (
            <section className="space-y-3">
              <h2 className="text-sm font-semibold text-slate-700">Latest result for each check</h2>
              {data.checks.map((check) => <CheckCard key={check.check_type} check={check} />)}
            </section>
          )}

          {data.history.length > 0 && (
            <section className="rounded-xl border border-slate-200 bg-white overflow-x-auto">
              <h2 className="px-4 py-3 text-sm font-semibold text-slate-700 border-b border-slate-100">Recent checks</h2>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">
                    <th className="px-4 py-2">When</th>
                    <th className="px-4 py-2">Check</th>
                    <th className="px-4 py-2">Result</th>
                    <th className="px-4 py-2 text-right">Differences</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.history.map((entry, i) => (
                    <tr key={`${entry.checked_at ?? 'unknown'}-${entry.check_type}-${i}`}>
                      <td className="px-4 py-2 text-slate-600 whitespace-nowrap">{formatWhen(entry.checked_at)}</td>
                      <td className="px-4 py-2 text-slate-800">{entry.label}</td>
                      <td className="px-4 py-2"><StateBadge state={entry.state} /></td>
                      <td className="px-4 py-2 text-right text-slate-700">{entry.mismatch_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          <AdvisoryNotice source={data.source} />
        </>
      ) : null}
    </InsightShell>
  );
}
