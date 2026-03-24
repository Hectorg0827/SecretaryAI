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

// ─── API surface ──────────────────────────────────────────────────────────────

export const api = {
  get:    <T>(path: string)               => request<T>('GET',    path),
  post:   <T>(path: string, body: unknown) => request<T>('POST',   path, body),
  patch:  <T>(path: string, body: unknown) => request<T>('PATCH',  path, body),
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

  auth: {
    login: (email: string, password: string) =>
      api.post<{ access_token: string; token_type: string; company_id: string; role: string }>(
        '/auth/login', { email, password }
      ),
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
