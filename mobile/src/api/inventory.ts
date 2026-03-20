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

export async function getInventoryAlerts(): Promise<{
  items: InventoryItem[];
  total: number;
}> {
  return apiFetch<{ items: InventoryItem[]; total: number }>('/api/inventory/alerts');
}
