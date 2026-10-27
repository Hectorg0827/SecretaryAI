import type { BriefingEntryV1, BriefingSectionV1, BriefingTodayResponseV1, DashboardSummary, Draft, FeedEvent, FollowUpNote, InventoryAlert, PriorityEmail, SalesSummary } from './api';

export const BRIEFING_SECTIONS: readonly BriefingSectionV1[] = [
  'red_alerts', 'yellow_alerts', 'opportunities', 'scoreboard', 'todays_priorities', 'actions_taken',
];

export type BriefingSales = Pick<SalesSummary, 'total_revenue' | 'order_count'>;
export type BriefingFeedEvent = Pick<FeedEvent, 'id' | 'title' | 'event_type' | 'action_type'>;
export type BriefingAuditEvent = {
  id: string;
  event_type: string;
  created_at: string;
  metadata?: Record<string, unknown>;
};
export type DashboardWorkflowRun = {
  run_id: string;
  workflow_name: string;
  status: string;
  current_step: number;
  total_steps: number;
  current_step_name: string;
};

// These are presentation-layer guards, not a replacement for the canonical
// backend contracts. An incomplete API payload must never become a plausible
// zero balance, healthy-inventory assertion, or empty approval queue.
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function count(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function nonempty(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function timestamp(value: unknown): value is string {
  return nonempty(value) && Number.isFinite(Date.parse(value));
}

function awareTimestamp(value: unknown): value is string {
  return timestamp(value) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value);
}

function exactFields(value: Record<string, unknown>, fields: readonly string[]): boolean {
  return Object.keys(value).length === fields.length && fields.every((field) => field in value);
}

/** Strict presentation boundary for Chat 1's partial, versioned executive projection. */
export function verifiedBriefingToday(value: unknown): BriefingTodayResponseV1 | null {
  if (!record(value) || !exactFields(value, [
    'contract_version', 'company_id', 'audience_role', 'created_at', 'sections',
    'source_coverage', 'complete_digest', 'source_quality',
    'snapshot_generated_at', 'snapshot_age_seconds',
  ])) return null;
  if (value.contract_version !== '1.0' || !nonempty(value.company_id)
    || !['owner', 'manager'].includes(String(value.audience_role))
    || !awareTimestamp(value.created_at) || !awareTimestamp(value.snapshot_generated_at)
    || value.source_coverage !== 'dashboard_snapshot_only' || value.complete_digest !== false
    || value.source_quality !== 'partial' || typeof value.snapshot_age_seconds !== 'number'
    || !Number.isFinite(value.snapshot_age_seconds) || value.snapshot_age_seconds < 0
    || value.snapshot_age_seconds > 86400
    || !record(value.sections) || !exactFields(value.sections, BRIEFING_SECTIONS)) return null;
  // The V1 API refuses snapshots older than 24 hours. The API's own UTC
  // created/snapshot timestamps should agree with its reported age; do not
  // trust a stale HTTP 200 payload that simply advertises a low age.
  const observedAge = (Date.parse(value.created_at) - Date.parse(value.snapshot_generated_at)) / 1000;
  if (observedAge < -300 || Math.abs(observedAge - value.snapshot_age_seconds) > 300) return null;

  const entryIsValid = (raw: unknown, section: BriefingSectionV1): raw is BriefingEntryV1 => {
    if (!record(raw) || !exactFields(raw, [
      'entry_id', 'section', 'title', 'status', 'detected_at', 'proposed_action_id', 'evidence',
    ])) return false;
    if (!nonempty(raw.entry_id) || raw.section !== section || !nonempty(raw.title)
      || !['open', 'resolved', 'needs_review', 'source_unavailable'].includes(String(raw.status))
      || !awareTimestamp(raw.detected_at)
      || (raw.proposed_action_id !== null && !nonempty(raw.proposed_action_id))
      || !Array.isArray(raw.evidence) || raw.evidence.length === 0) return false;
    const validEvidence = raw.evidence.every((item: unknown) => record(item)
      && exactFields(item, ['source_system', 'source_record_id', 'fetched_at', 'quality', 'confidence'])
      && nonempty(item.source_system) && nonempty(item.source_record_id)
      && awareTimestamp(item.fetched_at)
      && ['verified', 'partial', 'unavailable'].includes(String(item.quality))
      && count(item.confidence) && item.confidence <= 100
      && (item.quality !== 'unavailable' || item.confidence === 0));
    if (!validEvidence) return false;
    const unavailable = raw.evidence.some((item: { quality: string }) => item.quality === 'unavailable');
    return unavailable === (raw.status === 'source_unavailable');
  };

  const sections = value.sections as Record<string, unknown>;
  if (!BRIEFING_SECTIONS.every((section) => Array.isArray(sections[section])
    && (sections[section] as unknown[]).every((entry: unknown) => entryIsValid(entry, section)))) return null;
  return value as unknown as BriefingTodayResponseV1;
}

function calendarDate(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function inventoryAlert(value: unknown): value is InventoryAlert {
  if (!record(value)) return false;
  return typeof value.item_id === 'string'
    && typeof value.product_name === 'string'
    && typeof value.total_qty === 'number'
    && Number.isFinite(value.total_qty)
    && value.total_qty >= 0
    && (value.weeks_remaining == null || (typeof value.weeks_remaining === 'number' && Number.isFinite(value.weeks_remaining)))
    && (value.stock_status === 'low' || value.stock_status === 'critical' || value.stock_status === 'out_of_stock');
}

function accountCounts(value: unknown): value is NonNullable<DashboardSummary['accounts']> {
  return record(value) && count(value.healthy) && count(value.slowing)
    && count(value.at_risk) && count(value.dormant);
}

export function verifiedDashboardSummary(value: unknown): DashboardSummary | null {
  if (!record(value) || !Array.isArray(value.inventory_alerts)) return null;
  const accounts = value.accounts;
  if (accounts !== null && !accountCounts(accounts)) return null;
  if (!value.inventory_alerts.every(inventoryAlert)) return null;

  // Optional counts are unknown if absent or malformed; never silently zero.
  const pendingActions = count(value.pending_actions) ? value.pending_actions : null;
  const unreadEmails = count(value.unread_emails) ? value.unread_emails : null;
  const generatedAt = timestamp(value.generated_at)
    ? value.generated_at : null;
  return {
    accounts: accounts === null ? null : {
      healthy: accounts.healthy, slowing: accounts.slowing,
      at_risk: accounts.at_risk, dormant: accounts.dormant,
    },
    inventory_alerts: value.inventory_alerts.map((item: InventoryAlert) => ({
      item_id: item.item_id,
      product_name: item.product_name,
      total_qty: item.total_qty,
      weeks_remaining: item.weeks_remaining ?? null,
      stock_status: item.stock_status,
      needs_po: item.needs_po === true,
    })),
    pending_actions: pendingActions,
    unread_emails: unreadEmails,
    generated_at: generatedAt,
    cached: value.cached === true,
  };
}

export function verifiedPendingDrafts(value: unknown): Draft[] | null {
  if (!record(value) || !Array.isArray(value.drafts)) return null;
  if (!value.drafts.every((draft: unknown) => record(draft)
    && nonempty(draft.id)
    && nonempty(draft.action_type)
    && draft.status === 'pending'
    && timestamp(draft.created_at)
    && record(draft.content))) return null;
  return value.drafts as Draft[];
}

// Only fields actually consumed by the read-only six-card briefing are
// accepted here; other source consumers keep their own API contract checks.
export function verifiedBriefingSales(value: unknown): BriefingSales | null {
  if (!record(value) || typeof value.total_revenue !== 'number'
    || !Number.isFinite(value.total_revenue) || !count(value.order_count)) return null;
  return { total_revenue: value.total_revenue, order_count: value.order_count };
}

export function verifiedBriefingNotes(value: unknown): { notes: FollowUpNote[] } | null {
  if (!record(value) || !Array.isArray(value.notes)) return null;
  if (!value.notes.every((note: unknown) => record(note)
    && nonempty(note.id)
    && typeof note.text === 'string'
    && typeof note.done === 'boolean'
    && timestamp(note.created_at)
    && (note.due_date == null || calendarDate(note.due_date)))) return null;
  return { notes: value.notes as FollowUpNote[] };
}

export function verifiedBriefingFeed(value: unknown): { events: BriefingFeedEvent[] } | null {
  if (!record(value) || !Array.isArray(value.events)) return null;
  if (!value.events.every((event: unknown) => record(event)
    && nonempty(event.id)
    && nonempty(event.title)
    && nonempty(event.event_type)
    && (event.action_type == null || typeof event.action_type === 'string'))) return null;
  return { events: value.events.map((event: Record<string, unknown>) => ({
    id: event.id as string,
    title: event.title as string,
    event_type: event.event_type as string,
    action_type: typeof event.action_type === 'string' ? event.action_type : null,
  })) };
}

export function verifiedBriefingApprovals(value: unknown): { drafts: Draft[] } | null {
  const drafts = verifiedPendingDrafts(value);
  return drafts === null ? null : { drafts };
}

export function verifiedBriefingAudit(value: unknown): { events: BriefingAuditEvent[] } | null {
  if (!record(value) || !Array.isArray(value.events)) return null;
  if (!value.events.every((event: unknown) => record(event)
    && nonempty(event.id)
    && nonempty(event.event_type)
    && timestamp(event.created_at)
    && (event.metadata == null || record(event.metadata)))) return null;
  return { events: value.events as BriefingAuditEvent[] };
}

export function verifiedSalesChart(value: unknown): SalesSummary | null {
  if (!verifiedBriefingSales(value) || !record(value)
    || typeof value.avg_order_value !== 'number' || !Number.isFinite(value.avg_order_value)
    || typeof value.vs_prior_period_pct !== 'number' || !Number.isFinite(value.vs_prior_period_pct)
    || typeof value.top_account !== 'string'
    || !Array.isArray(value.chart_data)) return null;
  if (!value.chart_data.every((point: unknown) => record(point)
    && calendarDate(point.date)
    && typeof point.revenue === 'number' && Number.isFinite(point.revenue)
    && count(point.order_count))) return null;
  return value as unknown as SalesSummary;
}

export function verifiedDashboardFeed(value: unknown): { events: FeedEvent[] } | null {
  if (!verifiedBriefingFeed(value) || !record(value) || !Array.isArray(value.events)) return null;
  if (!value.events.every((event: unknown) => record(event)
    && typeof event.body === 'string'
    && (event.priority === 'high' || event.priority === 'medium' || event.priority === 'low')
    && typeof event.is_read === 'boolean'
    && (event.action_label == null || typeof event.action_label === 'string'))) return null;
  return { events: value.events as FeedEvent[] };
}

export function verifiedPriorityEmails(value: unknown): { emails: PriorityEmail[] } | null {
  if (!record(value) || !Array.isArray(value.emails)) return null;
  if (!value.emails.every((email: unknown) => record(email)
    && nonempty(email.id)
    && nonempty(email.thread_id)
    && typeof email.from === 'string'
    && typeof email.from_email === 'string'
    && typeof email.subject === 'string'
    && typeof email.snippet === 'string'
    && typeof email.ai_summary === 'string'
    && (email.ai_priority === 'high' || email.ai_priority === 'medium' || email.ai_priority === 'low')
    && typeof email.ai_action_needed === 'string'
    && timestamp(email.received_at)
    && typeof email.is_read === 'boolean'
    && Array.isArray(email.labels)
    && email.labels.every((label: unknown) => typeof label === 'string'))) return null;
  return { emails: value.emails as PriorityEmail[] };
}

export function verifiedWorkflowRuns(value: unknown): { workflows: DashboardWorkflowRun[] } | null {
  if (!record(value) || !Array.isArray(value.workflows)) return null;
  if (!value.workflows.every((run: unknown) => record(run)
    && nonempty(run.run_id)
    && nonempty(run.workflow_name)
    && nonempty(run.status)
    && nonempty(run.current_step_name)
    && count(run.current_step)
    && count(run.total_steps) && run.total_steps > 0
    && (run.current_step as number) <= (run.total_steps as number))) return null;
  return { workflows: value.workflows as DashboardWorkflowRun[] };
}
