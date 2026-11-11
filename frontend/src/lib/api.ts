/// <reference types="vite/client" />
/**
 * API client for the SecretaryAI backend.
 * All requests are authenticated via JWT Bearer token.
 */

import {
  API_BASE, authHeaders, clearSession, credentialOptions, ensureSession, getCsrf, getToken,
  saveSession, sessionMode,
} from './session';

let _refreshing: Promise<boolean> | null = null;

/**
 * Exchange the current session for a fresh one. Returns true if the caller
 * may retry its request.
 */
async function _tryRefresh(): Promise<boolean> {
  const cookieMode = sessionMode() === 'cookie';
  const token = getToken();
  if (!cookieMode && !token) return false;
  if (cookieMode && !(await ensureSession())) return false;
  const csrfBefore = getCsrf();
  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders('POST') },
      // cookie mode: the token travels in the cookie, never in the body
      body: JSON.stringify(cookieMode ? {} : { access_token: token }),
      ...credentialOptions(),
    });
    if (!res.ok) {
      // Refresh works once. If another browser tab refreshed first it has
      // already stored the newer session — use that instead of signing out.
      if (cookieMode) {
        sessionStorage.removeItem('secretary_csrf');
        return (await ensureSession()) && getCsrf() !== csrfBefore;
      }
      const latest = getToken();
      return !!latest && latest !== token;
    }
    const data = await res.json();
    if (cookieMode || data.access_token) {
      saveSession(data);
      return true;
    }
  } catch {
    // network error — fall through
  }
  return false;
}

/** Turn any error body from the backend into one plain sentence. */
function errorMessage(body: unknown, status: number): string {
  const b = (body ?? {}) as { detail?: unknown; message?: unknown };
  if (typeof b.detail === 'string' && b.detail) return b.detail;
  if (typeof b.message === 'string' && b.message) return b.message;
  if (Array.isArray(b.detail) && b.detail[0]?.msg) return String(b.detail[0].msg);
  if (status === 402) return 'Your trial or subscription has ended. The account owner can choose a plan in Settings.';
  if (status === 403) return 'You do not have permission to do that.';
  if (status === 429) return 'Too many attempts. Please wait a moment and try again.';
  if (status >= 500) return 'Something went wrong on our side. Please try again in a moment.';
  return `Request failed (${status})`;
}

/**
 * Sign-in style endpoints answer 401 for "wrong password" / "bad code". That
 * is an answer to show the user, not an expired session to refresh.
 */
function isPublicAuthPath(path: string): boolean {
  return /^\/auth\/(login|register|refresh|forgot-password|reset-password|accept-invite|verify-email|resend-verification)/.test(path);
}

function send(method: string, path: string, body?: unknown): Promise<Response> {
  return fetch(`${API_BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...authHeaders(method) },
    body: body ? JSON.stringify(body) : undefined,
    ...credentialOptions(),
  });
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const publicPath = isPublicAuthPath(path);
  // Cookie mode, fresh tab: fetch this tab's CSRF value before a request that changes something.
  if (!publicPath && sessionMode() === 'cookie' && method !== 'GET' && !getCsrf()) {
    await ensureSession();
  }
  const response = await send(method, path, body);

  if (response.status === 401 && !publicPath) {
    // Attempt a silent refresh once
    if (!_refreshing) {
      _refreshing = _tryRefresh().finally(() => { _refreshing = null; });
    }
    if (await _refreshing) {
      const retry = await send(method, path, body);
      if (retry.ok) return retry.json();
    }
    // Refresh failed — sign out locally and go to the sign-in page
    clearSession();
    window.location.href = '/login';
    throw new Error('Session expired');
  }

  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(errorMessage(error, response.status));
  }

  return response.json();
}

// ─── Types ────────────────────────────────────────────────────────────────────

export interface FeedEvent {
  id: string;
  event_type: string;
  title: string;
  body: string;
  priority: 'high' | 'medium' | 'low';
  action_label: string | null;
  action_type: string | null;
  action_data: Record<string, unknown>;
  entity_name: string | null;
  is_read: boolean;
  created_at: string;
}

export interface DashboardSummary {
  accounts: { healthy: number; slowing: number; at_risk: number; dormant: number };
  inventory_alerts: InventoryAlert[];
  pending_actions: number;
  unread_emails: number;
  /** ISO timestamp of when the QB-derived data was last computed by the scheduler */
  generated_at: string | null;
  /** true when data came from the shared snapshot cache (not recomputed live) */
  cached: boolean;
}

export interface InventoryAlert {
  item_id: string;
  product_name: string;
  total_qty: number;
  weeks_remaining: number | null;
  stock_status: 'low' | 'critical' | 'out_of_stock';
  needs_po: boolean;
}

export interface PriorityEmail {
  id: string;
  thread_id: string;
  from: string;
  from_email: string;
  subject: string;
  snippet: string;
  ai_summary: string;
  ai_priority: 'high' | 'medium' | 'low';
  ai_action_needed: string;
  received_at: string;
  is_read: boolean;
  labels: string[];
}

export interface SalesDataPoint {
  date: string;
  revenue: number;
  order_count: number;
}

export interface SalesSummary {
  total_revenue: number;
  order_count: number;
  avg_order_value: number;
  top_account: string;
  vs_prior_period_pct: number;
  chart_data: SalesDataPoint[];
}

export interface Account {
  id: string;
  name: string;
  email: string;
  phone: string;
  state: string;
  health_status: 'healthy' | 'slowing' | 'at_risk' | 'dormant' | 'unknown';
  health_score: number;
  last_order_date: string | null;
  current_balance: number;
  avg_order_value: number;
  assigned_rep: string | null;
  assigned_rep_id?: string | null;
}

export interface Draft {
  id: string;
  action_type: string;
  content: Record<string, unknown>;
  created_at: string;
  status: 'pending' | 'approved' | 'rejected';
}

export interface AgentStatus {
  connected: boolean;
  last_seen: string | null;
  agent_version: string | null;
  platform: string | null;
}

export interface FollowUpNote {
  id: string;
  text: string;
  account_name?: string;
  due_date?: string;
  done: boolean;
  created_at: string;
}

export interface IntegrationStatus {
  connected: boolean;
  connected_at?: string | null;
  realm_id?: string | null;
  end_user_id?: string | null;
}

export interface IntegrationsResponse {
  quickbooks_online:  IntegrationStatus;
  quickbooks_desktop: IntegrationStatus;
  gmail:              IntegrationStatus;
  google_sheets:      IntegrationStatus;
  outlook?:           IntegrationStatus & { account?: string | null };
}

/** What really happened when a draft was approved. */
export interface ApprovalResult {
  draft_id: string;
  status: string;
  already_processed?: boolean;
  execution_status?: 'executed' | 'manual_required' | 'failed' | 'unknown';
  execution_detail?: string;
}

/**
 * Plain-language outcome of an approval. `ok` is false when the action did NOT
 * happen (the draft is back in the queue), so the UI must not show success.
 */
export function describeApproval(r: ApprovalResult | undefined | null): { ok: boolean; done: boolean; message: string } {
  if (!r) return { ok: true, done: true, message: 'Approved' };
  if (r.already_processed) return { ok: true, done: true, message: 'This was already handled.' };
  if (r.execution_status === 'failed') {
    return { ok: false, done: false, message: r.execution_detail || 'Approved, but it could not be carried out. It is still in your queue.' };
  }
  if (r.execution_status === 'unknown') {
    // We cannot tell whether it happened. It leaves the queue (so it cannot be
    // sent twice by a second click) but is clearly NOT reported as success.
    return { ok: false, done: true, message: r.execution_detail || 'We could not confirm whether this was completed. Please check before doing it again.' };
  }
  if (r.execution_status === 'manual_required') {
    return { ok: true, done: true, message: r.execution_detail || 'Approved. Please complete this step yourself.' };
  }
  return { ok: true, done: true, message: r.execution_detail || 'Approved and done' };
}

export interface PurchaseOrderOptions {
  item: { id: string; name: string; unit_cost: number; on_hand: number; weekly_sell_rate: number; suggested_qty: number } | null;
  vendors: { id: string; name: string }[];
  preferred_vendor_id: string | null;
}

export interface TeamMember {
  id: string;
  name: string | null;
  email: string;
  role: 'owner' | 'manager' | 'sales_rep' | 'back_office' | 'viewer';
  is_active: boolean;
  two_factor: boolean;
  pending_invite: boolean;
  invited_at: string | null;
  last_login_at: string | null;
}

export interface InviteResult {
  user?: TeamMember;
  email_sent: boolean;
  invite_link?: string;
  note?: string;
}

type Session = { access_token?: string; csrf_token?: string | null; session?: string; company_id: string; role: string };

export interface InboxItem {
  id: string;
  type: 'email' | 'approval' | 'alert' | 'health_event';
  priority: 'high' | 'medium' | 'low';
  title: string;
  body: string;
  source_id?: string;
  account_name?: string;
  is_read: boolean;
  created_at: string;
  metadata?: Record<string, unknown>;
}

// ── Compliance types ──────────────────────────────────────────────────────────

export interface ComplianceStatus { overall: 'ok'|'warning'|'critical'; critical_alerts: number; warning_alerts: number; active_states: number; total_products: number; deadlines_due_7_days: number; state_status: Record<string,string> }
export interface ComplianceAlert { id: string; alert_type: string; priority: 'critical'|'warning'|'info'; state_code: string; item_name: string; days_until: number; expiry_date: string; message: string; action_required: string; renewal_url: string; estimated_fee: number }
export interface ComplianceDeadline { deadline_type: string; state_code: string; due_date: string; frequency: string; action: string; notes: string }
export interface ComplianceDigest { date: string; critical_alerts: ComplianceAlert[]; warning_alerts: ComplianceAlert[]; upcoming_deadlines: ComplianceDeadline[]; state_status: Record<string,string>; cost_estimate_q: { licenses: number; brand_registrations: number; grand_total: number } }
export interface ShipmentCheckRequest { product_id: string; state_code: string; quantity_cases: number }
export interface ComplianceIssue { severity: string; issue: string; action: string; fee: number; state_code: string; category: string }
export interface ComplianceCheckResult { approved: boolean; issues: ComplianceIssue[]; fees: Record<string,number>; total_compliance_cost: number }
export interface StateInfo { state_code: string; state_name: string; is_control_spirits: boolean; is_control_wine: boolean; brand_registration_required: boolean; priority_tier: number; regulator_url: string }
export interface StateRules { state_code: string; state_name: string; regulator_name: string; franchise_law: { exists: boolean; termination: string; notice_days: number }; excise_tax_per_gallon: Record<string,number>; is_control_spirits: boolean; is_control_wine: boolean }
export interface FeeEstimate { licenses: number; brand_registrations: number; grand_total: number; by_state: Record<string, Record<string,number>> }
export interface DistributorRisk { franchise_law_exists: boolean; termination_restriction: string; risk_level: 'low'|'medium'|'high'; notice_days_required: number; notes: string }
export interface ComplianceProduct { id: string; sku: string; name: string; product_type: string; abv_pct: number; country_of_origin: string }
export interface StateLicense { id: string; state_code: string; license_type: string; license_number: string; expiration_date: string; status: string; annual_fee: number }
export interface BrandRegistration { id: string; product_id: string; state_code: string; registration_number: string; expiration_date: string; status: string }
export interface FederalPermit { id: string; permit_type: string; permit_number: string; expiration_date: string; status: string }

// ─── API surface ──────────────────────────────────────────────────────────────

export interface ConnectorRegistration {
  id: string;
  company_id: string;
  connector_type: string;
  connector_id: string;
  version: string;
  status: 'connected' | 'stale' | 'error' | 'disconnected';
  last_heartbeat: string | null;
  capabilities: string[];
  registered_at: string;
}

export interface ConnectorSyncLog {
  id: string;
  sync_type: string;
  entity_type: string;
  status: string;
  rows_fetched: number;
  rows_upserted: number;
  started_at: string;
  completed_at: string | null;
}

export const api = {
  get:    <T>(path: string)               => request<T>('GET',    path),
  post:   <T>(path: string, body: unknown) => request<T>('POST',   path, body),
  patch:  <T>(path: string, body: unknown) => request<T>('PATCH',  path, body),
  put:    <T>(path: string, body: unknown) => request<T>('PUT',    path, body),
  delete:         <T>(path: string)               => request<T>('DELETE', path),
  deleteWithBody: <T>(path: string, body: unknown) => request<T>('DELETE', path, body),

  connectors: {
    status:              () => api.get<{ connectors: ConnectorRegistration[]; local_connector_enabled?: boolean }>('/api/connectors/status'),
    detail:              (id: string) => api.get<{ connector: ConnectorRegistration; sync_logs: ConnectorSyncLog[] }>(`/api/connectors/status/${id}`),
    generateSecret:      () => api.post<{ install_secret: string; warning: string }>('/api/connectors/generate-install-secret', {}),
    dispatchTask:        (task_type: string, parameters?: Record<string, unknown>) =>
                           api.post<{ task_id: string; status: string; issued_at: string }>('/api/connectors/dispatch-task', { task_type, parameters: parameters ?? {} }),
  },

  dashboard: {
    summary:  ()          => api.get<DashboardSummary>('/api/dashboard/summary'),
    refresh:  ()          => api.post<{ status: string }>('/api/dashboard/refresh', {}),
    sales:    (days = 30) => api.get<SalesSummary>(`/api/dashboard/sales?days=${days}`),
    emails:   ()          => api.get<{ emails: PriorityEmail[] }>('/api/dashboard/emails'),
    notes:    ()          => api.get<{ notes: FollowUpNote[] }>('/api/dashboard/notes'),
    addNote:  (text: string, accountName?: string, dueDate?: string) =>
      api.post<FollowUpNote>('/api/dashboard/notes', { text, account_name: accountName, due_date: dueDate }),
    doneNote: (id: string) =>
      api.patch<FollowUpNote>(`/api/dashboard/notes/${id}`, { done: true }),
  },

  emails: {
    draftReply: (emailId: string) =>
      api.post<{ draft: string; subject: string }>('/api/dashboard/emails/draft-reply', { email_id: emailId }),
    markRead: (emailId: string) =>
      api.post<void>(`/api/dashboard/emails/${emailId}/mark-read`, {}),
    sendReply: (emailId: string, draft: string, subject: string, to: string) =>
      api.post<void>('/api/dashboard/emails/send-reply', { email_id: emailId, body: draft, subject, to }),
  },

  actions: {
    pending: () => api.get<{ drafts: Draft[] }>('/api/actions/pending'),
    approve: (id: string, edits?: Record<string, unknown>) =>
      api.post<ApprovalResult>(`/api/actions/approve/${id}`, edits ? { edited_content: edits } : {}),
    reject:  (id: string, reason?: string) =>
      api.post<void>(`/api/actions/reject/${id}`, { reason: reason ?? 'Rejected by user' }),
  },

  inbox: {
    feed:     (filter?: string) =>
      api.get<{ items: InboxItem[]; unread_count: number }>(
        `/api/inbox${filter && filter !== 'all' ? `?type=${filter}` : ''}`,
      ),
    markRead: (id: string) => api.patch<void>(`/api/inbox/${id}/read`, {}),
  },

  accounts: {
    list: () => api.get<{ accounts: Account[]; can_assign?: boolean }>('/api/accounts/'),
    reps: () => api.get<{ reps: { id: string; name: string; role: string }[] }>('/api/accounts/reps'),
    assignRep: (accountId: string, userId: string | null) =>
      api.put<{ account_id: string; assigned_rep_id: string | null; assigned_rep: string | null }>(
        `/api/accounts/${encodeURIComponent(accountId)}/rep`, { user_id: userId }),
  },

  purchaseOrders: {
    options: (itemId: string) => api.get<PurchaseOrderOptions>(`/api/actions/po-options?item_id=${encodeURIComponent(itemId)}`),
    draft: (body: { item_id: string; product_name: string; vendor_id?: string | null; qty?: number; unit_cost?: number; notes?: string }) =>
      api.post<{ status: string; draft_id: string; ready_for_quickbooks: boolean; message: string }>('/api/actions/draft-po', body),
  },

  inventory: {
    list: () => api.get<{ items: InventoryAlert[] }>('/api/inventory'),
  },

  agent: {
    status: () => api.get<AgentStatus>('/api/agent/status'),
  },

  settings: {
    integrations: () => api.get<IntegrationsResponse>('/api/settings/integrations'),
    qboConnectUrl: () => api.get<{ url: string }>('/auth/qbo/connect-url'),
    qboDisconnect: () => api.delete<{ status: string }>('/auth/qbo/disconnect'),
    gmailConnectUrl: () => api.get<{ url: string }>('/auth/gmail/connect-url'),
    gmailDisconnect: () => api.delete<{ status: string }>('/auth/gmail/disconnect'),
    outlookConnectUrl: () => api.get<{ url: string }>('/auth/outlook/connect-url'),
    outlookDisconnect: () => api.delete<{ status: string }>('/auth/outlook/disconnect'),
    saveQBD: (endUserId: string) =>
      api.post<{ status: string; end_user_id: string }>('/api/settings/integrations/qbd', { end_user_id: endUserId }),
    disconnectQBD: () => api.delete<{ status: string }>('/api/settings/integrations/qbd'),
  },

  setup: {
    qbdStart: () =>
      api.post<{ auth_flow_url: string; end_user_id: string; connection_id: string }>(
        '/api/setup/qb-desktop/start', {}
      ),
    qbdStatus: () =>
      api.get<{ connected: boolean; status: string; message: string }>(
        '/api/setup/qb-desktop/status'
      ),
  },

  feed: {
    list:    (unreadOnly = false) => api.get<{ events: FeedEvent[]; unread_count: number }>(`/api/feed/?unread_only=${unreadOnly}`),
    read:    (id: string) => api.post<{ status: string }>(`/api/feed/${id}/read`, {}),
    dismiss: (id: string) => api.post<{ status: string }>(`/api/feed/${id}/dismiss`, {}),
  },

  auth: {
    login: (email: string, password: string, totp_code?: string) =>
      api.post<
        | (Session & { requires_2fa?: false })
        | { requires_2fa: true; pre_auth_token: string }
      >('/auth/login', { email, password, ...(totp_code ? { totp_code } : {}) }),
    forgotPassword: (email: string) =>
      api.post<{ status: string; message: string }>('/auth/forgot-password', { email }),
    resetPassword: (token: string, new_password: string) =>
      api.post<{ status: string; message: string }>('/auth/reset-password', { token, new_password }),
    acceptInvite: (token: string, password: string, full_name: string) =>
      api.post<Session>('/auth/accept-invite', { token, password, full_name }),
    verifyEmail: (token: string) =>
      api.post<{ status: string; message: string }>('/auth/verify-email', { token }),
    logout: () => api.post<{ status: string }>('/auth/logout', {}),
    changePassword: (current_password: string, new_password: string) =>
      api.post<Session & { status: string }>('/auth/change-password', { current_password, new_password }),
  },

  users: {
    list: () => api.get<{ users: TeamMember[]; roles: string[] }>('/api/users'),
    invite: (email: string, role: string, name: string) =>
      api.post<InviteResult>('/api/users/invite', { email, role, name }),
    resendInvite: (id: string) => api.post<InviteResult>(`/api/users/${id}/resend-invite`, {}),
    update: (id: string, changes: { role?: string; is_active?: boolean; name?: string }) =>
      api.patch<{ user: TeamMember }>(`/api/users/${id}`, changes),
  },

  // ── Compliance types ──────────────────────────────────────────────────────────

  compliance: {
    getStatus: () => api.get<ComplianceStatus>('/api/compliance/status'),
    getAlerts: (days = 90) => api.get<ComplianceAlert[]>(`/api/compliance/alerts?days=${days}`),
    getDeadlines: (days = 30) => api.get<ComplianceDeadline[]>(`/api/compliance/deadlines?days=${days}`),
    getDigest: () => api.get<ComplianceDigest>('/api/compliance/digest'),
    checkShipment: (body: ShipmentCheckRequest) => api.post<ComplianceCheckResult>('/api/compliance/check-shipment', body),
    listStates: () => api.get<StateInfo[]>('/api/compliance/states'),
    getStateRules: (code: string) => api.get<StateRules>(`/api/compliance/states/${code}`),
    getFeeEstimate: (states: string[]) => api.get<FeeEstimate>(`/api/compliance/fee-estimate?states=${states.join(',')}`),
    getDistributorRisk: (state: string) => api.get<DistributorRisk>(`/api/compliance/distributor-risk/${state}`),
    getProducts: () => api.get<ComplianceProduct[]>('/api/compliance/products'),
    getLicenses: () => api.get<StateLicense[]>('/api/compliance/licenses'),
    getBrandRegistrations: () => api.get<BrandRegistration[]>('/api/compliance/brand-registrations'),
    getFederalPermits: () => api.get<FederalPermit[]>('/api/compliance/federal-permits'),

    // Setup
    getSetupStatus: () => api.get<{is_setup: boolean; missing: string[]; products_count: number; licenses_count: number; federal_permits_count: number; distributors_count: number; brand_registrations_count: number}>('/api/compliance/setup-status'),

    // CRUD — products
    createProduct: (body: object) => api.post<object>('/api/compliance/products', body),
    updateProduct: (id: string, body: object) => api.put<object>(`/api/compliance/products/${id}`, body),
    deleteProduct: (id: string) => api.delete<{deleted: boolean}>(`/api/compliance/products/${id}`),

    // CRUD — licenses
    createLicense: (body: object) => api.post<object>('/api/compliance/licenses', body),
    updateLicense: (id: string, body: object) => api.put<object>(`/api/compliance/licenses/${id}`, body),
    deleteLicense: (id: string) => api.delete<{deleted: boolean}>(`/api/compliance/licenses/${id}`),

    // CRUD — brand registrations
    createBrandRegistration: (body: object) => api.post<object>('/api/compliance/brand-registrations', body),
    updateBrandRegistration: (id: string, body: object) => api.put<object>(`/api/compliance/brand-registrations/${id}`, body),
    deleteBrandRegistration: (id: string) => api.delete<{deleted: boolean}>(`/api/compliance/brand-registrations/${id}`),

    // CRUD — federal permits
    createFederalPermit: (body: object) => api.post<object>('/api/compliance/federal-permits', body),
    updateFederalPermit: (id: string, body: object) => api.put<object>(`/api/compliance/federal-permits/${id}`, body),
    deleteFederalPermit: (id: string) => api.delete<{deleted: boolean}>(`/api/compliance/federal-permits/${id}`),

    // CRUD — distributors
    getDistributors: () => api.get<{count: number; distributors: object[]}>('/api/compliance/distributors'),
    createDistributor: (body: object) => api.post<object>('/api/compliance/distributors', body),
    updateDistributor: (id: string, body: object) => api.put<object>(`/api/compliance/distributors/${id}`, body),
    deleteDistributor: (id: string) => api.delete<{deleted: boolean}>(`/api/compliance/distributors/${id}`),

    // CRUD — COLAs (Certificate of Label Approval)
    getColas: () => api.get<object[]>('/api/compliance/colas'),
    createCola: (body: object) => api.post<object>('/api/compliance/colas', body),
    updateCola: (id: string, body: object) => api.put<object>(`/api/compliance/colas/${id}`, body),
    deleteCola: (id: string) => api.delete<{deleted: boolean}>(`/api/compliance/colas/${id}`),

    // Import
    importRows: (body: {entity_type: string; rows: object[]}) => api.post<{imported: number; errors: object[]; total_rows: number}>('/api/compliance/import', body),
  },

  logistics: {
    reorderQueue: (skuData: unknown[]) =>
      api.post('/api/logistics/reorder/evaluate', { skus: skuData }),
    generatePos: (packages: unknown[], supplierConfigs: Record<string, unknown>) =>
      api.post('/api/logistics/po/generate', { approved_packages: packages, supplier_configs: supplierConfigs }),
    parseVendorResponse: (data: unknown) =>
      api.post('/api/logistics/vendor/parse-response', data),
    parseFreightInvoice: (data: unknown) =>
      api.post('/api/logistics/freight/parse-invoice', data),
    logCustomsHold: (data: unknown) =>
      api.post('/api/logistics/customs/hold', data),
    demurrageRisk: (data: unknown) =>
      api.post('/api/logistics/customs/demurrage-risk', data),
    processReceipt: (data: unknown) =>
      api.post('/api/logistics/receipt/process', data),
    reconcileCosts: (data: unknown) =>
      api.post('/api/logistics/cost/reconcile', data),
    monthlyReport: () =>
      api.get('/api/logistics/report/monthly'),
    describeModule: () =>
      api.get('/api/logistics/describe'),
  },

  /** Streaming chat — async generator of SSE events with text and conversationId */
  async *streamChat(
    message: string,
    conversationId?: string,
  ): AsyncGenerator<{ text: string; conversationId?: string }> {
    if (sessionMode() === 'cookie' && !getCsrf()) await ensureSession();
    const response = await fetch(`${API_BASE}/api/chat/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders('POST') },
      body: JSON.stringify({ message, conversation_id: conversationId }),
      ...credentialOptions(),
    });

    if (!response.ok || !response.body) {
      throw new Error(`Chat request failed: ${response.status}`);
    }

    const reader  = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';   // keep incomplete last line
      for (const line of lines) {
        if (line.startsWith('data: ') && !line.includes('[DONE]')) {
          try {
            const payload = JSON.parse(line.slice(6));
            yield {
              text: payload.text ?? '',
              conversationId: payload.conversation_id,
            };
          } catch {
            // non-JSON line — skip
          }
        }
      }
    }
  },
};
