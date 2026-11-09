/**
 * Lola client ⇄ orchestration contract (Workstream 4 ⇄ Workstream 1).
 *
 * This file is the single source of truth for what the Lola UI expects from
 * the backend. Workstream 1 owns policy, permissions and execution; the client
 * only renders what the server says and NEVER invents business results.
 *
 * Wire format: `POST /api/lola/turn` returns `text/event-stream`, one JSON
 * `LolaEvent` per `data:` line. See docs/lola/CONTRACT.md.
 *
 * Until that endpoint ships, the HTTP transport falls back to the legacy
 * `/api/chat/message` stream and maps its `{text}` / `{action}` / `{done}`
 * events onto this contract.
 */

export type LolaLanguage = 'en' | 'es';

/** Visual + semantic states of Lola's orb. */
export type OrbState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'working'
  | 'awaiting_approval'
  | 'attention'
  | 'error';

/** Where a piece of data came from, so the user can judge it. */
export interface Evidence {
  source: string;            // e.g. "QuickBooks Online", "Carrier API: Maersk"
  as_of?: string | null;     // ISO timestamp of the source data
  record_ref?: string;       // e.g. "INV-10442"
  href?: string;             // deep link into the existing Lion BI page
}

/**
 * Data availability. Anything other than `ok` must be rendered as a
 * non-success state — never as an empty-but-green result.
 */
export type Availability = 'ok' | 'partial' | 'stale' | 'unavailable' | 'forbidden';

interface CardBase {
  id: string;
  title: string;
  availability: Availability;
  /** Human explanation when availability !== 'ok'. */
  availability_note?: string;
  evidence?: Evidence[];
  /**
   * Server-assigned sensitivity. The client additionally hides `financial`
   * and `executive` cards from roles without `view_financials` (defence in
   * depth only — the server remains the authority).
   */
  sensitivity?: 'standard' | 'financial' | 'executive';
}

export interface MetricCard extends CardBase {
  kind: 'metrics';
  metrics: { label: string; value: string; delta?: string; tone?: 'good' | 'bad' | 'neutral' }[];
}

export interface ChartCard extends CardBase {
  kind: 'chart';
  chart: 'line' | 'bar';
  x_key: string;
  series: { key: string; label: string }[];
  data: Record<string, string | number>[];
  value_format?: 'currency' | 'number' | 'percent';
}

export interface TableCard extends CardBase {
  kind: 'table';
  columns: { key: string; label: string; align?: 'left' | 'right' }[];
  rows: Record<string, string | number>[];
  total_label?: string;
}

export interface TimelineCard extends CardBase {
  kind: 'shipment_timeline';
  reference: string;            // container / BOL / PO
  carrier?: string;
  eta?: string;
  steps: {
    label: string;
    at?: string | null;
    status: 'done' | 'current' | 'pending' | 'exception';
    detail?: string;
  }[];
  exceptions?: string[];
}

export interface ExplanationCard extends CardBase {
  kind: 'financial_explanation';
  headline: string;
  drivers: { label: string; impact: string; direction: 'up' | 'down' }[];
}

export type EvidenceCard = MetricCard | ChartCard | TableCard | TimelineCard | ExplanationCard;

/** A proposed action. Lola may only PROPOSE; execution needs approval. */
export interface ActionPreview {
  id: string;                    // server-issued draft/action id
  action_type: string;           // e.g. "send_collection_email"
  title: string;
  summary: string;
  fields: { label: string; value: string }[];
  risk: 'low' | 'medium' | 'high';
  /** Server's verdict on whether THIS user may approve it. */
  can_approve: boolean;
  /** Why the user cannot approve, if they cannot. */
  approval_note?: string;
  /** `submitted` = accepted for execution but not yet verified in the system of record. */
  status: 'proposed' | 'executing' | 'submitted' | 'executed' | 'rejected' | 'failed';
}

/**
 * Execution receipt. `verified` is only true when the server confirmed the
 * effect in the system of record (e.g. email accepted by Gmail, PO created in
 * QuickBooks). The UI never shows "done" for an unverified receipt.
 */
export interface Receipt {
  action_id: string;
  status: 'executed' | 'submitted' | 'failed';
  verified: boolean;
  message: string;
  reference?: string;
  executed_at?: string;
}

export type NoticeKind = 'permission_denied' | 'connector_outage' | 'data_unavailable' | 'info';

export interface Notice {
  kind: NoticeKind;
  message: string;
  connector?: string;
}

/** Server → client streaming events. */
export type LolaEvent =
  | { type: 'conversation'; conversation_id: string }
  | { type: 'status'; state: 'thinking' | 'working'; step?: string }
  | { type: 'text'; delta: string }
  | { type: 'language'; language: LolaLanguage }
  | { type: 'card'; card: EvidenceCard }
  | { type: 'action_preview'; action: ActionPreview }
  | { type: 'receipt'; receipt: Receipt }
  | { type: 'notice'; notice: Notice }
  | { type: 'suggestions'; items: string[] }
  | { type: 'error'; message: string; retryable: boolean }
  | { type: 'done' };

export interface Attachment {
  name: string;
  size: number;
  mime: string;
  /** Present only for small text files the client may inline. */
  text?: string;
}

export interface TurnRequest {
  message: string;
  conversation_id?: string;
  language: LolaLanguage;
  channel: 'text' | 'voice';
  /** Set when the user interrupted/corrected the previous turn. */
  supersedes_turn?: boolean;
  attachments?: Attachment[];
  /** Page the user is on, so Lola can answer "this account" etc. */
  context_route?: string;
  /** User's retention choices; the server applies (and may tighten) them. */
  retention?: { transcript: TranscriptRetention; audio: AudioRetention };
}

export type TranscriptRetention = 'none' | 'session' | 'saved';
export type AudioRetention = 'none' | '30d';

// ── Command center briefing ──────────────────────────────────────────────────

export type BriefingSectionId =
  | 'morning_briefing'
  | 'critical_alerts'
  | 'performance'
  | 'department_priorities'
  | 'logistics_exceptions'
  | 'financial_risks'
  | 'pending_approvals'
  | 'next_actions'
  | 'recent_tasks';

export interface BriefingItem {
  id: string;
  title: string;
  detail?: string;
  severity?: 'critical' | 'warning' | 'info' | 'good';
  /** Prompt to hand to Lola when the user taps "Ask Lola". */
  ask?: string;
  href?: string;
  meta?: string;
}

export interface BriefingSection {
  id: BriefingSectionId;
  title: string;
  availability: Availability;
  availability_note?: string;
  items: BriefingItem[];
  evidence?: Evidence[];
  sensitivity?: 'standard' | 'financial' | 'executive';
}

export interface Briefing {
  generated_at: string;
  greeting: string;
  headline: string;
  sections: BriefingSection[];
  /** true only for scripted demo data — rendered with a visible badge. */
  demo?: boolean;
}
