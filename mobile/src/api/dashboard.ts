import { apiFetch } from './client';

export interface DashboardSummary {
  accounts: {
    total: number;
    at_risk: number;
    healthy: number;
    new_this_month: number;
  };
  inventory_alerts: InventoryAlert[];
  pending_actions: number;
  unread_emails: number;
}

export interface InventoryAlert {
  item_id: string;
  product_name: string;
  stock_status: 'critical' | 'low' | 'healthy';
  total_qty: number;
  weeks_remaining: number | null;
}

export interface SalesData {
  total_revenue: number;
  order_count: number;
  avg_order_value: number;
  top_account: string | null;
  vs_prior_period_pct: number | null;
  chart_data: { date: string; revenue: number }[];
}

export interface Email {
  id: string;
  from_name: string;
  from_email: string;
  subject: string;
  snippet: string;
  received_at: string;
  is_read: boolean;
  thread_id: string;
}

export interface FollowUpNote {
  id: string;
  text: string;
  account_name: string | null;
  due_date: string | null;
  done: boolean;
  created_at: string;
}

export async function getDashboardSummary(): Promise<DashboardSummary> {
  return apiFetch<DashboardSummary>('/api/dashboard/summary');
}

export async function getSalesData(days = 30): Promise<SalesData> {
  return apiFetch<SalesData>(`/api/dashboard/sales?days=${days}`);
}

export async function getEmails(): Promise<{ emails: Email[] }> {
  return apiFetch<{ emails: Email[] }>('/api/dashboard/emails');
}

export async function markEmailRead(emailId: string): Promise<void> {
  await apiFetch(`/api/dashboard/emails/${emailId}/mark-read`, { method: 'POST' });
}

export async function draftEmailReply(
  emailId: string,
): Promise<{ draft: string; subject: string }> {
  return apiFetch(`/api/dashboard/emails/draft-reply`, {
    method: 'POST',
    body: JSON.stringify({ email_id: emailId }),
  });
}

export async function sendEmailReply(params: {
  email_id: string;
  body: string;
  subject: string;
  to: string;
}): Promise<void> {
  await apiFetch('/api/dashboard/emails/send-reply', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function getNotes(): Promise<{ notes: FollowUpNote[] }> {
  return apiFetch<{ notes: FollowUpNote[] }>('/api/dashboard/notes');
}

export async function createNote(params: {
  text: string;
  account_name?: string;
  due_date?: string;
}): Promise<void> {
  await apiFetch('/api/dashboard/notes', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function updateNote(noteId: string, done: boolean): Promise<void> {
  await apiFetch(`/api/dashboard/notes/${noteId}`, {
    method: 'PATCH',
    body: JSON.stringify({ done }),
  });
}
