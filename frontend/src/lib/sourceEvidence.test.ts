import { describe, expect, it } from 'vitest';
import { verifiedDashboardSummary, verifiedPendingDrafts } from './sourceEvidence';

const validSummary = () => ({
  accounts: { healthy: 5, slowing: 2, at_risk: 1, dormant: 0 },
  inventory_alerts: [{ item_id: 'fixture-sku', product_name: 'Synthetic item', stock_status: 'low', total_qty: 4, weeks_remaining: 2, needs_po: false }],
  pending_actions: 2,
  unread_emails: 1,
  generated_at: '2026-10-26T12:00:00Z',
  cached: true,
});

describe('presentation source-evidence guards (synthetic fixtures only)', () => {
  it('accepts a properly structured summary with source timestamp', () => {
    expect(verifiedDashboardSummary(validSummary())).toMatchObject({
      accounts: { at_risk: 1 }, pending_actions: 2, cached: true,
      inventory_alerts: [{ product_name: 'Synthetic item' }],
    });
  });

  it('rejects incomplete account counts and malformed inventory instead of inventing zeros', () => {
    expect(verifiedDashboardSummary({ ...validSummary(), accounts: { healthy: 0 } })).toBeNull();
    expect(verifiedDashboardSummary({ ...validSummary(), accounts: { healthy: 5, slowing: -1, at_risk: 0, dormant: 0 } })).toBeNull();
    expect(verifiedDashboardSummary({ ...validSummary(), inventory_alerts: null })).toBeNull();
    expect(verifiedDashboardSummary({ ...validSummary(), inventory_alerts: [{ stock_status: 'critical' }] })).toBeNull();
  });

  it('marks absent, invalid, or unverified optional metrics as unknown', () => {
    const result = verifiedDashboardSummary({
      ...validSummary(), pending_actions: '0', unread_emails: undefined,
      generated_at: 'not-a-date', cached: 'false',
    });
    expect(result).toMatchObject({ pending_actions: null, unread_emails: null, generated_at: null, cached: false });
  });

  it('accepts a verified empty approval array but rejects malformed responses', () => {
    expect(verifiedPendingDrafts({ drafts: [] })).toEqual([]);
    expect(verifiedPendingDrafts({})).toBeNull();
    expect(verifiedPendingDrafts({ drafts: null })).toBeNull();
    expect(verifiedPendingDrafts({ drafts: [{ id: 'draft', content: {}, action_type: 'draft_purchase_order' }] })).toBeNull();
    expect(verifiedPendingDrafts({ drafts: [{
      id: 'fixture-draft', action_type: 'draft_purchase_order',
      content: { subtotal: 10 }, status: 'pending', created_at: '2026-10-26T12:00:00Z',
    }] })).toHaveLength(1);
  });
});
