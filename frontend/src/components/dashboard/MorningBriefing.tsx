import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { api, type DashboardSummary, type FollowUpNote } from '../../lib/api';
import {
  verifiedBriefingSales, verifiedBriefingNotes, verifiedBriefingFeed,
  verifiedBriefingApprovals, verifiedBriefingAudit,
  type BriefingSales, type BriefingFeedEvent, type BriefingAuditEvent,
} from '../../lib/sourceEvidence';

type ReadState<T> = { status: 'loading' | 'ready' | 'unavailable'; data: T | null };
const loading = <T,>(): ReadState<T> => ({ status: 'loading', data: null });
const settled = <T,>(result: PromiseSettledResult<unknown>, verify: (value: unknown) => T | null): ReadState<T> => {
  const data = result.status === 'fulfilled' ? verify(result.value) : null;
  return data === null ? { status: 'unavailable', data: null } : { status: 'ready', data };
};

export type BriefingSources = {
  sales: ReadState<BriefingSales>;
  notes: ReadState<{ notes: FollowUpNote[] }>;
  feed: ReadState<{ events: BriefingFeedEvent[] }>;
  approvals: ReadState<{ drafts: { id: string }[] }>;
  audit: ReadState<{ events: BriefingAuditEvent[] }>;
};

function localDay(date = new Date()): string {
  const year = date.getFullYear();
  return `${year}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

export function dueFollowUps(notes: FollowUpNote[], today: string): FollowUpNote[] {
  return notes.filter((note) => !note.done && note.due_date && note.due_date.slice(0, 10) <= today);
}

export function verifiedOpportunities(events: BriefingFeedEvent[]): BriefingFeedEvent[] {
  // Priority alone does not establish an opportunity. Require explicit classification.
  return events.filter((event) => event.event_type === 'opportunity' || event.action_type === 'opportunity');
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

function SourceUnavailable({ status }: { status: ReadState<unknown>['status'] }) {
  return <p className="text-sm text-amber-800" role="status">
    {status === 'loading' ? 'Checking source…' : 'Source unavailable. No conclusion can be drawn.'}
  </p>;
}

/** Owner/manager digest, strictly read-only. All counts come from existing API responses. */
export function MorningBriefing({ summary, summaryLoading }: {
  summary: DashboardSummary | null;
  summaryLoading: boolean;
}) {
  const [sources, setSources] = useState<BriefingSources>({
    sales: loading(), notes: loading(), feed: loading(), approvals: loading(), audit: loading(),
  });

  useEffect(() => {
    let current = true;
    async function readSources() {
      const results = await Promise.allSettled([
        api.dashboard.sales(30),
        api.dashboard.notes(),
        api.feed.list(),
        api.actions.pending(),
        api.get<unknown>('/api/admin/audit-log?page=1&page_size=25'),
      ] as const);
      if (!current) return;
      setSources({
        sales: settled(results[0], verifiedBriefingSales),
        notes: settled(results[1], verifiedBriefingNotes),
        feed: settled(results[2], verifiedBriefingFeed),
        approvals: settled(results[3], verifiedBriefingApprovals),
        audit: settled(results[4], verifiedBriefingAudit),
      });
    }
    void readSources();
    return () => { current = false; };
  }, []);

  const summaryStatus = summaryLoading ? 'loading' : summary ? 'ready' : 'unavailable';
  const summarySource = summary?.generated_at
    ? `Dashboard snapshot · ${new Date(summary.generated_at).toLocaleString()}${summary.cached ? ' · cached' : ''}`
    : 'Dashboard summary · snapshot freshness not verified';
  const criticalStock = summary?.inventory_alerts.filter((item) => item.stock_status === 'critical' || item.stock_status === 'out_of_stock') ?? [];
  const watchStock = summary?.inventory_alerts.filter((item) => item.stock_status === 'low') ?? [];
  const opportunities = verifiedOpportunities(sources.feed.data?.events ?? []);
  const dueNotes = dueFollowUps(sources.notes.data?.notes ?? [], localDay());
  const actionEvents = (sources.audit.data?.events ?? []).filter((event) =>
    /^(draft_(approved|rejected)|workflow_(failed|completed)|policy_blocked)$/.test(event.event_type),
  ).slice(0, 4);

  return <section className="space-y-3" aria-labelledby="briefing-heading">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div>
        <h2 id="briefing-heading" className="text-lg font-bold text-slate-900">Your five-minute morning briefing</h2>
        <p className="text-xs text-slate-600">Six views · company-scoped API responses · missing evidence is explicitly marked</p>
      </div>
      <span className="text-xs rounded-full border border-slate-300 px-3 py-1 text-slate-700">Read-only briefing</span>
    </div>
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
      <BriefingCard title="1 · Red flags" tone="red" source={summarySource} link={{ to: '/inventory', label: 'Review inventory' }}>
        {summary ? <ul className="space-y-1 list-disc pl-4">
          <li>{summary.accounts.at_risk} at-risk accounts</li>
          <li>{summary.accounts.dormant} dormant accounts</li>
          <li>{criticalStock.length} critical or out-of-stock inventory alerts</li>
        </ul> : <SourceUnavailable status={summaryStatus} />}
      </BriefingCard>
      <BriefingCard title="2 · Yellow watchlist" tone="yellow" source={summarySource} link={{ to: '/accounts', label: 'Review accounts' }}>
        {summary ? <ul className="space-y-1 list-disc pl-4">
          <li>{summary.accounts.slowing} slowing accounts</li>
          <li>{watchStock.length} low-stock alerts</li>
          <li>Counts describe the snapshot, not a live stock verification.</li>
        </ul> : <SourceUnavailable status={summaryStatus} />}
      </BriefingCard>
      <BriefingCard title="3 · Opportunities" tone="green" source="Company insights feed · explicit opportunity classification only">
        {sources.feed.data ? (opportunities.length ? <ul className="space-y-2">
          {opportunities.slice(0, 3).map((event) => <li key={event.id}>{event.title}</li>)}
        </ul> : <p>No explicitly classified opportunities in the returned feed.</p>) : <SourceUnavailable status={sources.feed.status} />}
      </BriefingCard>
      <BriefingCard title="4 · Scoreboard" tone="blue" source="Sales API · last 30 days · QuickBooks/provider freshness unverified">
        {sources.sales.data ? <>
          <p className="text-2xl font-bold text-slate-900">{new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(sources.sales.data.total_revenue)}</p>
          <p>{sources.sales.data.order_count} reported orders</p>
          <p className="text-xs text-slate-600 mt-1">Do not treat a zero as confirmed until connector health is verified.</p>
        </> : <SourceUnavailable status={sources.sales.status} />}
      </BriefingCard>
      <BriefingCard title="5 · Today’s decisions" tone="yellow" source="Pending approvals + follow-up notes · separate live reads" link={{ to: '/inbox', label: 'Open approval inbox' }}>
        {sources.approvals.data ? <p><strong>{sources.approvals.data.drafts.length}</strong> drafts waiting for human approval</p>
          : <SourceUnavailable status={sources.approvals.status} />}
        {sources.notes.data ? <p className="mt-1"><strong>{dueNotes.length}</strong> unfinished follow-ups due today or overdue</p>
          : <SourceUnavailable status={sources.notes.status} />}
      </BriefingCard>
      <BriefingCard title="6 · Actions & failures" tone="slate" source="Tenant audit events · latest 25 · authorization and execution are distinct" link={{ to: '/audit-log', label: 'Open audit trail' }}>
        {sources.audit.data ? (actionEvents.length ? <ul className="space-y-2">
          {actionEvents.map((event) => <li key={event.id} className="text-xs">
            <span className="font-semibold">{event.event_type.replace(/_/g, ' ')}</span>
            {typeof event.metadata?.execution_status === 'string' ? ` · ${event.metadata.execution_status}` : ' · execution not confirmed'}
            <span className="block text-slate-500">{new Date(event.created_at).toLocaleString()}</span>
          </li>)}
        </ul> : <p>No matching action events in the latest 25 audit records.</p>) : <SourceUnavailable status={sources.audit.status} />}
      </BriefingCard>
    </div>
  </section>;
}
