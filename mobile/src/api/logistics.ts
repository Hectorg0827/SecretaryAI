import { ApiError, AuthError, apiFetch } from './client';
import { getInventoryAlerts, InventoryAlert } from './inventory';

// The Logistics screen reads only endpoints the backend actually serves:
//   GET /api/inventory/alerts          low / critical / out-of-stock products
//   GET /api/logistics/orders          saved purchase orders (owner, manager, back office)
//   GET /api/logistics/orders/{po}     one purchase order with ordered/received cases
// There is no backend source for shipment tracking or landed-cost variances,
// so the app does not show them rather than inventing numbers.

export type { InventoryAlert };

export interface PurchaseOrderSummary {
  id: string;
  po_number: string;
  supplier_ref: string | null;
  status: string;
  warehouse_id: string | null;
  created_at: string | null;
  approved_at: string | null;
}

export interface PurchaseOrderDetail {
  order: PurchaseOrderSummary;
  lines: { sku: string; qty_ordered: number | string; qty_received: number | string }[];
  ordered_cases: string;
  received_cases: string;
  remaining_cases: string;
  inventory_posted: boolean;
  accounting_reconciled: boolean;
}

export const getStockAlerts = async (): Promise<InventoryAlert[]> => {
  const data = await getInventoryAlerts();
  if (!Array.isArray(data?.alerts)) throw new Error('Stock alerts response was incomplete');
  return data.alerts;
};

export const getPurchaseOrders = async (): Promise<PurchaseOrderSummary[]> => {
  const data = await apiFetch<{ orders: PurchaseOrderSummary[]; count: number }>('/api/logistics/orders');
  if (!Array.isArray(data?.orders)) throw new Error('Purchase orders response was incomplete');
  return data.orders;
};

export const getPurchaseOrder = (poNumber: string) =>
  apiFetch<PurchaseOrderDetail>(`/api/logistics/orders/${encodeURIComponent(poNumber)}`);

/**
 * Plain-language message for a failed read. Access and outage responses are
 * explained instead of being shown as an empty (all clear) list.
 */
export function logisticsErrorMessage(err: unknown, subject: string): string {
  if (err instanceof AuthError) return err.message;
  if (err instanceof ApiError) {
    if (err.status === 403) {
      return `Your role does not have access to ${subject}. Ask the account owner if you need it.`;
    }
    if (err.status === 503) {
      return `${capitalize(subject)} can't be loaded right now because the data source is unavailable. `
        + 'Nothing is shown rather than guessing. Pull down to try again.';
    }
    if (err.status === 402) {
      return `${capitalize(subject)} requires an active subscription.`;
    }
    if (err.status === 404) {
      return `${capitalize(subject)} could not be found.`;
    }
  }
  return err instanceof Error && err.message
    ? `Couldn't load ${subject}: ${err.message}`
    : `Couldn't load ${subject}. Pull down to try again.`;
}

function capitalize(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

/** Most urgent first: out of stock, then critical, then low. */
export function sortStockAlerts(alerts: InventoryAlert[]): InventoryAlert[] {
  const rank: Record<string, number> = { out_of_stock: 0, critical: 1, low: 2 };
  return [...alerts].sort(
    (a, b) => (rank[a.stock_status] ?? 3) - (rank[b.stock_status] ?? 3)
      || a.product_name.localeCompare(b.product_name),
  );
}

export function urgentAlertCount(alerts: InventoryAlert[]): number {
  return alerts.filter((a) => a.stock_status === 'out_of_stock' || a.stock_status === 'critical').length;
}

const PO_STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  approved: 'Approved',
  partially_received: 'Partly received',
  received: 'Received',
};

export function purchaseOrderStatusLabel(status: string): string {
  if (PO_STATUS_LABELS[status]) return PO_STATUS_LABELS[status];
  const words = status.replace(/_/g, ' ').trim();
  return words ? capitalize(words) : 'Unknown';
}
