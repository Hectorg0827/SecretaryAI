import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { api, type BriefingEntryV1, type BriefingSectionV1, type BriefingTodayResponseV1 } from '../../lib/api';
import { BRIEFING_SECTIONS, verifiedBriefingToday } from '../../lib/sourceEvidence';
import { useAuth } from '../../hooks/useAuth';

type BriefingState = { status: 'loading' | 'ready' | 'unavailable'; data: BriefingTodayResponseV1 | null };

const SECTION_TEXT: Record<BriefingSectionV1, string> = {
  red_alerts: 'No risk entries in this partial snapshot. This is not an all-clear.',
  yellow_alerts: 'No watchlist entries in this partial snapshot. Other sources are not covered.',
  opportunities: 'Opportunity sources are not covered by the current snapshot.',
  scoreboard: 'Scoreboard evidence unavailable; no financial conclusion is supported.',
  todays_priorities: 'Current task and approval sources are not covered by this partial snapshot.',
  actions_taken: 'No verified execution receipts are covered. A queue is not proof of delivery.',
};

function Entries({ section, data }: { section: BriefingSectionV1; data: BriefingTodayResponseV1 }) {
  const entries: BriefingEntryV1[] = data.sections[section];
  if (!entries.length) return <p>{SECTION_TEXT[section]}</p>;
  return <ul className="space-y-3">
    {entries.map((entry) => <li key={entry.entry_id} className="space-y-1">
      <p className="font-semibold">{entry.title}</p>
      <p className="text-xs text-amber-800">{entry.status === 'source_unavailable'
        ? 'Source unavailable' : entry.status === 'needs_review' ? 'Needs review — not independently verified'
          : `Reported status: ${entry.status} (partial evidence)`}</p>
      {entry.evidence.map((evidence) => <p key={`${evidence.source_system}:${evidence.source_record_id}`}
        className="text-[11px] text-slate-600">
        Evidence: {evidence.source_system} · {evidence.quality} · {new Date(evidence.fetched_at).toLocaleString()} · confidence {evidence.confidence}/100
      </p>)}
    </li>)}
  </ul>;
}

function SourceLine({ children }: { children: ReactNode }) {
  return <p className="mt-auto pt-3 border-t border-slate-100 text-[11px] text-slate-600">{children}</p>;
}

function BriefingCard({ title, tone, children, source, link }: {
  title: string;
  tone: 'red' | 'yellow' | 'green' | 'blue' | 'slate';
  children: ReactNode;
  source: string;
  link?: { to: string; label: string };
}) {
  const bar = { red: 'bg-red-500', yellow: 'bg-amber-500', green: 'bg-emerald-500', blue: 'bg-blue-500', slate: 'bg-slate-500' }[tone];
  return <section className="min-w-0 flex flex-col gap-2 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" aria-label={title}>
    <div className="flex items-center gap-2">
      <span className={`h-2.5 w-2.5 rounded-full ${bar}`} aria-hidden="true" />
      <h3 className="text-sm font-bold text-slate-900">{title}</h3>
    </div>
    <div className="flex-1 text-sm text-slate-700">{children}</div>
    {link && <Link className="text-xs font-semibold text-blue-700 underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-blue-600" to={link.to}>{link.label}</Link>}
    <SourceLine>{source}</SourceLine>
  </section>;
}

function SourceUnavailable({ status }: { status: BriefingState['status'] }) {
  return <p className="text-sm text-amber-800" role="status">
    {status === 'loading' ? 'Checking canonical source…' : 'Source unavailable. No operational conclusion can be drawn.'}
  </p>;
}

/** Canonical executive read; browser aggregates are not allowed to synthesize this digest. */
export function MorningBriefing() {
  const { role } = useAuth();
  const [state, setState] = useState<BriefingState>({ status: 'loading', data: null });
  const requestEpoch = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++requestEpoch.current;
    setState({ status: 'loading', data: null });
    try {
      const data = verifiedBriefingToday(await api.briefing.today());
      if (!data || data.audience_role !== role) throw new Error('Briefing contract or role mismatch');
      if (requestId === requestEpoch.current) setState({ status: 'ready', data });
    } catch {
      if (requestId === requestEpoch.current) setState({ status: 'unavailable', data: null });
    }
  }, [role]);

  useEffect(() => {
    void load();
    const tracker = requestEpoch;
    return () => { tracker.current++; };
  }, [load]);

  const data = state.data;
  const status = state.status;
  const contents = (section: BriefingSectionV1) => data
    ? <Entries section={section} data={data} /> : <SourceUnavailable status={status} />;
  const evidenceSource = data ? 'Canonical v1 · dashboard snapshot only · partial source quality'
    : 'Canonical v1 · source unavailable';

  return <section className="space-y-3" aria-labelledby="briefing-heading">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div>
        <h2 id="briefing-heading" className="text-lg font-bold text-slate-900">Your five-minute morning briefing</h2>
        <p className="text-xs text-slate-600">Six canonical sections · server-scoped executive read · no outbound actions</p>
      </div>
      <button type="button" onClick={() => void load()} disabled={status === 'loading'}
        className="text-xs rounded-full border border-slate-300 px-3 py-1 text-slate-700 hover:bg-slate-100 disabled:opacity-50">
        {status === 'loading' ? 'Checking source…' : 'Retry briefing read'}
      </button>
    </div>
    {status === 'unavailable' && <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
      Canonical briefing unavailable. The source may be missing, stale, unauthorized, or incomplete (including HTTP 503).
      No zero activity, cleared risks, or completed actions can be inferred.
    </div>}
    {data && <div role="status" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
      <strong>Partial briefing — not a complete daily digest.</strong>{' '}
      Contract {data.contract_version} · source_coverage: {data.source_coverage} ·
      source_quality: {data.source_quality} · complete_digest: {String(data.complete_digest)}.
      <span className="block mt-1">
        Snapshot {new Date(data.snapshot_generated_at).toLocaleString()} ({Math.round(data.snapshot_age_seconds / 60)} minutes old).
        Independent provider completeness, current sales, live tasks and execution receipts are not verified.
      </span>
    </div>}
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
      <BriefingCard title="1 · Red flags" tone="red" source={evidenceSource} link={{ to: '/inventory', label: 'Review inventory' }}>
        {contents(BRIEFING_SECTIONS[0])}
      </BriefingCard>
      <BriefingCard title="2 · Yellow watchlist" tone="yellow" source={evidenceSource} link={{ to: '/accounts', label: 'Review accounts' }}>
        {contents(BRIEFING_SECTIONS[1])}
      </BriefingCard>
      <BriefingCard title="3 · Opportunities" tone="green" source={evidenceSource}>
        {contents(BRIEFING_SECTIONS[2])}
      </BriefingCard>
      <BriefingCard title="4 · Scoreboard" tone="blue" source={evidenceSource}>
        {contents(BRIEFING_SECTIONS[3])}
      </BriefingCard>
      <BriefingCard title="5 · Today’s decisions" tone="yellow" source={evidenceSource} link={{ to: '/inbox', label: 'Open approval inbox' }}>
        {contents(BRIEFING_SECTIONS[4])}
      </BriefingCard>
      <BriefingCard title="6 · Actions & failures" tone="slate" source={evidenceSource} link={{ to: '/audit-log', label: 'Open audit trail' }}>
        {contents(BRIEFING_SECTIONS[5])}
      </BriefingCard>
    </div>
  </section>;
}
