/**
 * Lola shared integration contracts — TypeScript mirror of
 * backend/app/lola/contracts.py (contract version 1.2.0).
 *
 * Source of truth: the Python models. JSON Schema: docs/lola/contracts.schema.json.
 * Example payloads for every status: docs/lola/examples.json.
 * A backend test fails if a model or status listed here goes missing.
 *
 * Rules every client must follow:
 *  - Always branch on `status`. Never treat an empty list as "nothing there".
 *  - Show `withheld` notes so users know when something was hidden and why.
 *  - An action is done ONLY when its receipt is `completed` AND `verified`.
 *  - Voice clients speak `speech`, never the raw `answer`.
 */

export const LOLA_CONTRACT_VERSION = "1.2.0";

export type LolaStatus =
  | "answered"
  | "partial"
  | "unavailable"
  | "denied"
  | "draft"
  | "pending_approval"
  | "approved"
  | "executing"
  | "completed"
  | "failed"
  | "rejected"
  | "needs_reconciliation";

export type Sensitivity = "public" | "internal" | "confidential" | "restricted";

export type Department =
  | "executive" | "sales" | "finance" | "operations" | "purchasing" | "hr" | "compliance" | "general";

export type OrgLevel = "executive" | "department_manager" | "employee";

export type ResourceType =
  | "customer_account" | "sales" | "receivables" | "inventory" | "inventory_cost" | "purchasing"
  | "financials" | "hr" | "payroll" | "memory" | "action" | "attachment";

export type RiskTier =
  | "read_only" | "draft" | "approval_required" | "routine_automation" | "high_risk" | "prohibited";

export type EntityType =
  | "customer" | "product" | "vendor" | "employee" | "purchase_order" | "invoice" | "territory" | "department";

export interface EvidenceRef {
  source_system: string;
  record_type: string;
  record_id: string | null;
  fetched_at: string;
  data_path: string;
  confidence: number;
  is_stale: boolean;
  note: string | null;
}

export interface BusinessEntity {
  entity_type: EntityType;
  entity_id: string;
  display_name: string;
  department: Department;
  sensitivity: Sensitivity;
  match_confidence: number;
  evidence: EvidenceRef[];
}

export interface PermissionDecision {
  allowed: boolean;
  resource_type: ResourceType;
  action: string;
  reason: string;
  basis: string[];
  scope: "company" | "department" | "team" | "own" | "delegated" | "none" | string;
  sensitivity_ceiling: Sensitivity;
  redacted_fields: string[];
  evaluated_at: string;
  policy_version: string;
}

export interface ToolResult {
  tool: string;
  department: Department;
  status: LolaStatus;
  summary: string;
  data: unknown;
  evidence: EvidenceRef[];
  authorization: PermissionDecision;
  sensitivity: Sensitivity;
  started_at: string;
  finished_at: string;
  error_code: string | null;
  warnings: string[];
}

export interface ActionProposal {
  proposal_id: string;
  action_type: string;
  risk_tier: RiskTier;
  title: string;
  description: string;
  payload: Record<string, unknown>;
  amount: string | null;
  entity: BusinessEntity | null;
  requested_by: string;
  company_id: string;
  idempotency_key: string;
  approval_roles: string[];
  requires_independent_approval: boolean;
  authorization: PermissionDecision;
  evidence: EvidenceRef[];
  created_at: string;
}

export interface ActionReceipt {
  receipt_id: string;
  proposal_id: string;
  company_id: string;
  action_type: string;
  risk_tier: RiskTier;
  status: LolaStatus;
  idempotency_key: string;
  requested_by: string;
  draft_id: string | null;
  approved_by: string | null;
  execution_id: string | null;
  external_reference: string | null;
  verified: boolean;
  error_code: string | null;
  history: Array<Record<string, unknown>>;
  created_at: string;
  updated_at: string;
}

export interface LolaContext {
  company_id: string;
  user_id: string;
  role: string;
  org_level: OrgLevel;
  department: Department;
  territories: string[];
  reports: string[];
  financial_clearance: boolean;
  hr_clearance: boolean;
  action_limit: string | null;
  active_delegation_ids: string[];
  conversation_id: string;
  channel: string;
  request_id: string;
  org_profile_available: boolean;
  built_at: string;
}

/** A small text file the client read and inlined (CSV, TSV, TXT, Markdown, JSON). Untrusted data. */
export interface LolaAttachment {
  name: string;
  mime?: string;
  size?: number;
  /** File text; omit for binary files (Lola reports them as unsupported). */
  text?: string | null;
}

export interface LolaRequest {
  message: string;
  conversation_id?: string | null;
  channel?: "chat" | "voice" | "mobile" | "desktop" | "api";
  /** Re-sending the same id never duplicates an action. */
  client_request_id?: string | null;
  /** false = answer only, never draft actions. */
  allow_actions?: boolean;
  /** Answer language. "auto" (default) detects English or Spanish from the message. */
  language?: "auto" | "en" | "es";
  /** Up to 5 files. Read as data only: never instructions, never stored. */
  attachments?: LolaAttachment[];
}

export interface LolaResponse {
  contract_version: string;
  request_id: string;
  conversation_id: string;
  status: LolaStatus;
  answer: string;
  speech: string;
  intents: string[];
  entities: BusinessEntity[];
  tool_results: ToolResult[];
  evidence: EvidenceRef[];
  proposals: ActionProposal[];
  receipts: ActionReceipt[];
  withheld: string[];
  memory_used: number;
  /** Language the answer is written in. */
  language: "en" | "es";
  created_at: string;
}

/** True only when a side effect is confirmed — never for approved/queued. */
export function isActionDone(receipt: ActionReceipt): boolean {
  return receipt.status === "completed" && receipt.verified === true;
}

/** Endpoints (all require the normal session token). */
export const LOLA_ENDPOINTS = {
  ask: "/api/lola/ask",
  me: "/api/lola/me",
  tools: "/api/lola/tools",
  contracts: "/api/lola/contracts",
  memory: "/api/lola/memory",
  actions: "/api/lola/actions",
  delegations: "/api/lola/delegations",
} as const;
