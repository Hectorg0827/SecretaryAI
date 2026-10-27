import type { DashboardSummary, Draft, InventoryAlert } from './api';

// These are presentation-layer guards, not a replacement for the canonical
// backend contracts. An incomplete API payload must never become a plausible
// zero balance, healthy-inventory assertion, or empty approval queue.
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function count(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function inventoryAlert(value: unknown): value is InventoryAlert {
  if (!record(value)) return false;
  return typeof value.item_id === 'string'
    && typeof value.product_name === 'string'
    && typeof value.total_qty === 'number'
    && Number.isFinite(value.total_qty)
    && value.total_qty >= 0
    && (value.weeks_remaining == null || (typeof value.weeks_remaining === 'number' && Number.isFinite(value.weeks_remaining)))
    && (value.stock_status === 'low' || value.stock_status === 'critical' || value.stock_status === 'out_of_stock');
}

export function verifiedDashboardSummary(value: unknown): DashboardSummary | null {
  if (!record(value) || !record(value.accounts) || !Array.isArray(value.inventory_alerts)) return null;
  const accounts = value.accounts;
  if (!count(accounts.healthy) || !count(accounts.slowing) || !count(accounts.at_risk) || !count(accounts.dormant)) return null;
  if (!value.inventory_alerts.every(inventoryAlert)) return null;

  // Optional counts are unknown if absent or malformed; never silently zero.
  const pendingActions = count(value.pending_actions) ? value.pending_actions : null;
  const unreadEmails = count(value.unread_emails) ? value.unread_emails : null;
  const timestamp = typeof value.generated_at === 'string' && Number.isFinite(Date.parse(value.generated_at))
    ? value.generated_at : null;
  return {
    accounts: { healthy: accounts.healthy, slowing: accounts.slowing, at_risk: accounts.at_risk, dormant: accounts.dormant },
    inventory_alerts: value.inventory_alerts.map((item: InventoryAlert) => ({
      item_id: item.item_id,
      product_name: item.product_name,
      total_qty: item.total_qty,
      weeks_remaining: item.weeks_remaining ?? null,
      stock_status: item.stock_status,
      needs_po: item.needs_po === true,
    })),
    pending_actions: pendingActions,
    unread_emails: unreadEmails,
    generated_at: timestamp,
    cached: value.cached === true,
  };
}

export function verifiedPendingDrafts(value: unknown): Draft[] | null {
  if (!record(value) || !Array.isArray(value.drafts)) return null;
  if (!value.drafts.every((draft: unknown) => record(draft)
    && typeof draft.id === 'string' && draft.id.length > 0
    && typeof draft.action_type === 'string' && draft.action_type.length > 0
    && typeof draft.created_at === 'string' && Number.isFinite(Date.parse(draft.created_at))
    && record(draft.content))) return null;
  return value.drafts as Draft[];
}
