import { apiFetch } from './client';

export interface ReorderAlert {
  sku: string;
  urgency: 'red' | 'yellow' | 'green';
  days_of_supply: number;
  recommended_qty: number;
  lead_time_p80_days: number;
  container_fill_pct: number;
}

export interface ShipmentStatus {
  booking_ref: string;
  vessel_name: string;
  eta: string;
  delay_days: number;
  status: string;
}

export interface CostVariance {
  component: string;
  change_pct: number;
  annualized_impact: number;
  severity: 'info' | 'warning' | 'alert' | 'critical';
}

export interface LogisticsOverview {
  reorder_alerts: ReorderAlert[];
  shipments: ShipmentStatus[];
  cost_variances: CostVariance[];
  total_landed_cost: number | null;
}

export const getReorderAlerts = () =>
  apiFetch<ReorderAlert[]>('/api/logistics/reorder-alerts');

export const getShipments = () =>
  apiFetch<ShipmentStatus[]>('/api/logistics/shipments');

export const getCostVariances = () =>
  apiFetch<{ variances: CostVariance[]; total_landed_cost: number | null }>('/api/logistics/cost-variances');

export const getMonthlyReport = () =>
  apiFetch<{ lead_time_summary: unknown; recommendation: string }>('/api/logistics/report/monthly');

export const describeLogisticsModule = () =>
  apiFetch<{ pipeline_stages: string[]; config: Record<string, unknown> }>('/api/logistics/describe');
