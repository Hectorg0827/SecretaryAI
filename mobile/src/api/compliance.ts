import { apiFetch } from './client';

// Types
export interface ComplianceAlert {
  id: string;
  alert_type: string;
  priority: 'critical' | 'warning' | 'info';
  state_code: string;
  item_name: string;
  days_until: number;
  message: string;
  action_required: string;
  estimated_fee: number;
}

export interface ComplianceDeadline {
  deadline_type: string;
  state_code: string;
  due_date: string;
  frequency: string;
  action: string;
}

export interface ComplianceStatus {
  overall: 'ok' | 'warning' | 'critical';
  critical_alerts: number;
  warning_alerts: number;
  active_states: number;
  total_products: number;
  deadlines_due_7_days: number;
  state_status: Record<string, string>;
}

export interface ShipmentCheck {
  product_id: string;
  state_code: string;
  quantity_cases: number;
}

export interface ComplianceCheckResult {
  approved: boolean;
  issues: Array<{ severity: string; issue: string; action: string }>;
  total_compliance_cost: number;
}

// API functions
export const getComplianceStatus = () =>
  apiFetch<ComplianceStatus>('/api/compliance/status');

export const getAlerts = (days = 90) =>
  apiFetch<ComplianceAlert[]>(`/api/compliance/alerts?days=${days}`);

export const getDeadlines = (days = 30) =>
  apiFetch<ComplianceDeadline[]>(`/api/compliance/deadlines?days=${days}`);

export const checkShipment = (body: ShipmentCheck) =>
  apiFetch<ComplianceCheckResult>('/api/compliance/check-shipment', {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const getDigest = () =>
  apiFetch<{ cost_estimate_q: { grand_total: number; licenses: number; brand_registrations: number } }>(
    '/api/compliance/digest'
  );
