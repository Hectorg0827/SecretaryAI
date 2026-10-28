import { apiFetch } from './client';

export interface InventoryItem {
  item_id: string;
  product_name: string;
  sku: string | null;
  qb_qty: number;
  warehouse_qty: number;
  total_qty: number;
  stock_status: 'critical' | 'low' | 'healthy';
  weeks_remaining: number | null;
  needs_po: boolean;
  weekly_sell_rate: number | null;
}

export async function getInventory(
  status?: 'critical' | 'low' | 'healthy',
): Promise<{ items: InventoryItem[]; total: number }> {
  const query = status ? `?status=${status}` : '';
  return apiFetch<{ items: InventoryItem[]; total: number }>(`/api/inventory${query}`);
}

/** One entry of GET /api/inventory/alerts (backend inventory._build_alert_dict). */
export interface InventoryAlert {
  item_id: string;
  product_name: string;
  total_qty: number;
  weeks_remaining: number | null;
  stock_status: 'low' | 'critical' | 'out_of_stock';
  needs_po: boolean;
}

export async function getInventoryAlerts(): Promise<{
  alerts: InventoryAlert[];
  count: number;
}> {
  return apiFetch<{ alerts: InventoryAlert[]; count: number }>('/api/inventory/alerts');
}
