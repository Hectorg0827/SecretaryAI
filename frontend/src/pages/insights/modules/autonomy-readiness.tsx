import type { InsightMeta } from '../registry';
import {
  AdvisoryNotice, ErrorState, InsightShell, LoadingState, useInsight, type AdvisoryFields,
} from '../ui';

export const meta: InsightMeta = {
  slug: 'autonomy-readiness',
  title: 'AI autonomy readiness',
  description: 'How reliable past AI suggestions were, and how much freedom each kind could safely be given.',
  group: 'Governance',
  // Backend: require_permission("approve_actions") → owner, manager.
  roles: ['owner', 'manager'],
};

type Assessment = 'ready' | 'keep_approval' | 'inform_only' | 'insufficient_history';

export interface AutonomyRow {
  action_type: string;
  label: string;
  proposals: number;
  decided: number;
  approved_as_is: number;
  approved_with_edits: number;
  rejected: number;
  pending: number;
  expired_unreviewed: number;
  as_is_rate: number | null;
  accepted_rate: number | null;
  rejected_rate: number | null;
  reliability_score: number | null;
  current_tier: number;
  current_level: string;
  recommended_tier: number | null;
  recommended_level: string | null;
  assessment: Assessment;
  reason: string;
}

export interface AutonomyReadiness extends AdvisoryFields {
  action_types: AutonomyRow[];
  rows_read: number;
  rows_skipped: number;
  total_decided: number;
  insufficient_history: boolean;
  ready_count: number;
  window_days: number;
  truncated: boolean;
  min_decisions: number;
  tiers: Record<string, string>;
  settings_changed: false;
}

const ASSESSMENT_STYLE: Record<Assessment, { label: string; className: string }> = {
  ready: { label: 'Could do more on its own', className: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  keep_approval: { label: 'Keep approving each one', className: 'bg-slate-50 text-slate-700 border-slate-200' },
  inform_only: { label: 'Show as information only', className: 'bg-amber-50 text-amber-700 border-amber-200' },
  insufficient_history: { label: 'Not enough history yet', className: 'bg-slate-50 text-slate-500 border-slate-200' },
};

function pct(value: number | null) {
  return value === null ? '—' : `${Math.round(value * 100)}%`;
}

function Row({ row }: { row: AutonomyRow }) {
  const style = ASSESSMENT_STYLE[row.assessment] ?? ASSESSMENT_STYLE.keep_approval;
  return (
    <tr className="border-b border-slate-100 align-top">
      <td className="py-2.5 px-4">
        <div className="text-sm font-medium text-slate-800">{row.label}</div>
        <div className="text-xs text-slate-400">{row.proposals} suggestion(s), {row.pending} waiting</div>
      </td>
      <td className="py-2.5 px-4 text-sm text-slate-700">{row.decided}</td>
      <td className="py-2.5 px-4 text-sm text-slate-700">{pct(row.as_is_rate)}</td>
      <td className="py-2.5 px-4 text-sm text-slate-700">{pct(row.rejected_rate)}</td>
      <td className="py-2.5 px-4 text-sm text-slate-700">{row.current_level}</td>
      <td className="py-2.5 px-4">
        <span className={`inline-block rounded-full border px-2 py-0.5 text-xs font-medium ${style.className}`}>
          {style.label}
        </span>
        {row.recommended_level && (
          <div className="text-sm font-semibold text-slate-800 mt-1">{row.recommended_level}</div>
        )}
        <div className="text-xs text-slate-500 mt-1 max-w-xs">{row.reason}</div>
      </td>
    </tr>
  );
}

export default function AutonomyReadinessPage() {
  const { data, error, loading, reload } = useInsight<AutonomyReadiness>('/api/insights/autonomy-readiness');

  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source={data?.source ?? 'connected_data'} />
      <p className="text-xs text-slate-500">
        This page only suggests. It never changes your approval settings — every AI suggestion keeps waiting for a person until you decide otherwise.
      </p>
      {loading && <LoadingState />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && !loading && (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="text-xs text-slate-500">Suggestions reviewed (last {data.window_days} days)</div>
              <div className="text-2xl font-bold text-slate-900">{data.total_decided}</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="text-xs text-slate-500">Kinds ready for more freedom</div>
              <div className="text-2xl font-bold text-slate-900">{data.ready_count}</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="text-xs text-slate-500">Records that couldn't be read</div>
              <div className="text-2xl font-bold text-slate-900">{data.rows_skipped}</div>
            </div>
          </div>
          {data.insufficient_history && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              Insufficient history: each kind of suggestion needs at least {data.min_decisions} reviewed examples before its reliability can be judged.
            </div>
          )}
          {data.truncated && (
            <p className="text-xs text-amber-700">Only the most recent suggestions were analysed.</p>
          )}
          {data.action_types.length > 0 ? (
            <div className="rounded-xl border border-slate-200 bg-white overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50">
                    {['Kind of suggestion', 'Reviewed', 'Approved unchanged', 'Rejected', 'Today', 'Suggested level'].map((h) => (
                      <th key={h} className="text-left py-2.5 px-4 text-xs font-medium text-slate-500">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.action_types.map((row) => <Row key={row.action_type} row={row} />)}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-slate-500">The AI hasn't proposed any actions for approval in this period.</p>
          )}
          <div className="rounded-xl border border-slate-200 bg-white p-4 text-xs text-slate-500 space-y-1">
            <div className="font-semibold text-slate-700">What the levels mean</div>
            <div><strong>Inform only:</strong> the AI shows the idea; nobody acts on it automatically.</div>
            <div><strong>Draft for approval:</strong> the AI prepares it and waits for a person (how SecretaryAI works today).</div>
            <div><strong>Act within limits:</strong> could act alone inside preset limits, such as orders under $5,000.</div>
            <div><strong>Autonomous:</strong> could act alone and only escalate unusual or critical cases.</div>
          </div>
        </>
      )}
    </InsightShell>
  );
}
