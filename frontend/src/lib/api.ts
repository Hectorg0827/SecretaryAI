/**
 * API client for the SecretaryAI backend.
 * All requests are authenticated via JWT Bearer token.
 */

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

function getToken(): string | null {
  return localStorage.getItem('secretary_token');
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

// ─── API surface ──────────────────────────────────────────────────────────────

export const api = {
  get:    <T>(path: string)               => request<T>('GET',    path),
  post:   <T>(path: string, body: unknown) => request<T>('POST',   path, body),
  patch:  <T>(path: string, body: unknown) => request<T>('PATCH',  path, body),
  delete: <T>(path: string)               => request<T>('DELETE', path),

  dashboard: {
    summary:  ()          => api.get<DashboardSummary>('/api/dashboard/summary'),
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
    sendReply: (emailId: string, draft: string, subject: string) =>
      api.post<void>('/api/dashboard/emails/send-reply', { email_id: emailId, body: draft, subject }),
  },

  actions: {
    pending: () => api.get<{ drafts: Draft[] }>('/api/actions/pending'),
    approve: (id: string, edits?: Record<string, unknown>) =>
      api.post<void>(`/api/actions/approve/${id}`, edits ?? {}),
    reject:  (id: string, reason?: string) =>
      api.post<void>(`/api/actions/reject/${id}`, { reason: reason ?? 'Rejected by user' }),
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

  /** Streaming chat — async generator of SSE text chunks */
  async *streamChat(message: string, conversationId?: string): AsyncGenerator<string> {
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

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const text = decoder.decode(value, { stream: true });
      for (const line of text.split('\n')) {
        if (line.startsWith('data: ') && !line.includes('[DONE]')) {
          yield line.slice(6);
        }
      }
    }
  },
};
