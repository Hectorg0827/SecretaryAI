/// <reference types="vite/client" />
/**
 * API client for the SecretaryAI backend.
 * All requests are authenticated via JWT Bearer token.
 */

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

function getToken(): string | null {
  return localStorage.getItem('secretary_token');
}

let _refreshing: Promise<string | null> | null = null;

async function _tryRefresh(): Promise<string | null> {
  const token = getToken();
  if (!token) return null;
  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ access_token: token }),
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (data.access_token) {
      localStorage.setItem('secretary_token', data.access_token);
      return data.access_token;
    }
  } catch {
    // network error — fall through
  }
  return null;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = getToken();
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (response.status === 401) {
    // Attempt a silent token refresh once
    if (!_refreshing) {
      _refreshing = _tryRefresh().finally(() => { _refreshing = null; });
    }
    const newToken = await _refreshing;
    if (newToken) {
      // Retry the original request with the new token
      const retry = await fetch(`${API_BASE}${path}`, {
        method,
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${newToken}` },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (retry.ok) return retry.json();
    }
    // Refresh failed — redirect to login
    localStorage.removeItem('secretary_token');
    window.location.href = '/login';
    throw new Error('Session expired');
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(error.detail ?? `HTTP ${response.status}`);
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
}

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

export const api = {
  get:    <T>(path: string)               => request<T>('GET',    path),
  post:   <T>(path: string, body: unknown) => request<T>('POST',   path, body),
  patch:  <T>(path: string, body: unknown) => request<T>('PATCH',  path, body),
  put:    <T>(path: string, body: unknown) => request<T>('PUT',    path, body),
  delete: <T>(path: string)               => request<T>('DELETE', path),

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
      api.post<void>(`/api/actions/approve/${id}`, edits ?? {}),
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
    list: () => api.get<{ accounts: Account[] }>('/api/accounts'),
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
        | { access_token: string; token_type: string; company_id: string; role: string; requires_2fa?: false }
        | { requires_2fa: true; pre_auth_token: string }
      >('/auth/login', { email, password, ...(totp_code ? { totp_code } : {}) }),
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
    const token = getToken();
    const response = await fetch(`${API_BASE}/api/chat/message`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ message, conversation_id: conversationId }),
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
