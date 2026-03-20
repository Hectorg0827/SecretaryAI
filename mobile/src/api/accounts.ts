import { apiFetch } from './client';

export interface Account {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  state: string | null;
  health_status: 'healthy' | 'at_risk' | 'churned' | 'new';
  health_score: number;
  last_order_date: string | null;
  current_balance: number;
  avg_order_value: number;
  assigned_rep: string | null;
}

export async function getAccounts(): Promise<{ accounts: Account[] }> {
  return apiFetch<{ accounts: Account[] }>('/api/accounts');
}

export async function getAccount(accountId: string): Promise<Account> {
  return apiFetch<Account>(`/api/accounts/${accountId}`);
}
