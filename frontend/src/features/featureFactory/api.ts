// =============================================================================
// SecretaryAI · Feature Factory — API Client
// =============================================================================
// Thin, typed wrapper over the backend endpoints. Uses the app's existing
// VITE_API_URL convention (see docker-compose.yml). Auth: this assumes your app
// already attaches the session/JWT (e.g. via a fetch wrapper or cookies). If you
// use a shared `apiFetch`, swap `request()` to call it instead.
// =============================================================================

import type {
  CreateFeatureResponse,
  FeatureView,
  PendingApproval,
  RunView,
} from "./types";

const BASE = (import.meta as any).env?.VITE_API_URL ?? "";
const ROOT = `${BASE}/api/feature-factory`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${ROOT}${path}`, {
    headers: { "Content-Type": "application/json" },
    credentials: "include", // send the existing session cookie
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const featureFactoryApi = {
  // Step 1
  createFeature: (requestText: string) =>
    request<CreateFeatureResponse>("/features", {
      method: "POST",
      body: JSON.stringify({ request_text: requestText }),
    }),

  // Step 2
  approveCapabilities: (featureId: string) =>
    request<{ ok: boolean }>(`/features/${featureId}/approve-capabilities`, {
      method: "POST",
    }),

  rejectFeature: (featureId: string) =>
    request<{ ok: boolean }>(`/features/${featureId}/reject`, { method: "POST" }),

  // Step 3
  dryRun: (featureId: string) =>
    request<{ run_id: string; planned_actions: RunView["planned_actions"] }>(
      `/features/${featureId}/dry-run`,
      { method: "POST" }
    ),

  // Step 4
  promoteObserve: (featureId: string) =>
    request<{ ok: boolean }>(`/features/${featureId}/promote-observe`, {
      method: "POST",
    }),
  promoteActive: (featureId: string) =>
    request<{ ok: boolean }>(`/features/${featureId}/promote-active`, {
      method: "POST",
    }),

  runNow: (featureId: string) =>
    request<{ run_id: string }>(`/features/${featureId}/run`, { method: "POST" }),

  // Step 6
  decideApproval: (approvalId: string, approve: boolean, note?: string) =>
    request<{ approved: boolean }>(`/approvals/${approvalId}/decide`, {
      method: "POST",
      body: JSON.stringify({ approve, note }),
    }),

  // Layer 7
  setKillSwitch: (enabled: boolean) =>
    request<{ ok: boolean; enabled: boolean }>(
      `/kill-switch?enabled=${enabled}`,
      { method: "POST" }
    ),

  // Reads
  listFeatures: () => request<FeatureView[]>("/features"),
  listRuns: (featureId: string) =>
    request<RunView[]>(`/features/${featureId}/runs`),
  listApprovals: () => request<PendingApproval[]>("/approvals"),
};
