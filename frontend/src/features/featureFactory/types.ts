// =============================================================================
// SecretaryAI · Feature Factory — Frontend Types
// =============================================================================
// These mirror backend/app/feature_factory/contracts.py EXACTLY. If the backend
// contract changes, change it here too. Keeping them in sync is what prevents
// the front/back drift our engineering rules call out as the #1 risk.
// =============================================================================

export type FeatureStatus =
  | "draft"
  | "pending_capability_approval"
  | "dry_run_ready"
  | "observe"
  | "active"
  | "disabled"
  | "failed"
  | "rejected";

export type RunMode = "dry_run" | "observe" | "act";
export type RunStatus = "success" | "failed" | "blocked" | "awaiting_approval";
export type ActionClass = "read" | "draft" | "commit" | "destructive";

export interface Capability {
  capability_key: string;
  resource: string;
  scope: ActionClass;
  human_summary: string;
}

export interface PlannedAction {
  action_type: string;
  action_class: ActionClass;
  target_ref: string;
  human_summary: string;
  params: Record<string, unknown>;
  requires_approval: boolean;
}

export interface CreateFeatureResponse {
  ok: boolean;
  feature_id?: string;
  description?: string;
  declared_capabilities?: Capability[];
  clarifying_question?: string | null;
  confidence?: number;
  validation?: {
    ok: boolean;
    issues: { severity: "error" | "warning"; code: string; message: string }[];
  };
}

export interface FeatureView {
  id: string;
  name: string;
  description: string;
  request_text: string;
  tier: number;
  status: FeatureStatus;
  declared_capabilities: Capability[];
  trigger_kind: string;
  schedule_cron: string | null;
  observe_runs_required: number;
  successful_observe_runs: number;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface RunView {
  id: string;
  feature_id: string;
  mode: RunMode;
  status: RunStatus;
  trigger: string;
  planned_actions: PlannedAction[];
  executed_actions: PlannedAction[];
  result_summary: string | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface PendingApproval {
  id: string;
  feature_id: string;
  action: PlannedAction;
  action_class: "commit" | "destructive";
  requested_at: string;
}

// Human-friendly labels for each status — used across the UI.
export const STATUS_LABEL: Record<FeatureStatus, string> = {
  draft: "Draft",
  pending_capability_approval: "Needs your approval",
  dry_run_ready: "Ready to preview",
  observe: "Watching (logging only)",
  active: "Active",
  disabled: "Turned off",
  failed: "Auto-disabled",
  rejected: "Declined",
};
