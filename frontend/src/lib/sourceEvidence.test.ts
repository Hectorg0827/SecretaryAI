import { describe, expect, it } from 'vitest';
import {
  verifiedDashboardSummary, verifiedPendingDrafts,
  verifiedBriefingSales, verifiedBriefingNotes, verifiedBriefingFeed,
  verifiedBriefingApprovals, verifiedBriefingAudit, verifiedSalesChart, verifiedDashboardFeed,
} from './sourceEvidence';

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

  it('requires finite financial totals and integer order counts without fabricating revenue', () => {
    expect(verifiedBriefingSales({ total_revenue: 0, order_count: 0 })).toEqual({ total_revenue: 0, order_count: 0 });
    expect(verifiedBriefingSales({ total_revenue: Infinity, order_count: 0 })).toBeNull();
    expect(verifiedBriefingSales({ total_revenue: '0', order_count: 0 })).toBeNull();
    expect(verifiedBriefingSales({ total_revenue: 100, order_count: -1 })).toBeNull();
  });

  it('rejects malformed note records and invalid calendar dates', () => {
    expect(verifiedBriefingNotes({ notes: [{
      id: 'note-1', text: 'Synthetic follow-up', done: false,
      due_date: '2026-10-26', created_at: '2026-10-20T08:00:00Z',
    }] })?.notes).toHaveLength(1);
    expect(verifiedBriefingNotes({ notes: [{ id: 'note-2', text: 'Something', done: 'false' }] })).toBeNull();
    expect(verifiedBriefingNotes({ notes: [{
      id: 'note-3', text: 'Something', done: false,
      created_at: '2026-10-20T08:00:00Z', due_date: '2026-02-30',
    }] })).toBeNull();
  });

  it('does not count arrays of unverified opportunities or approval drafts', () => {
    expect(verifiedBriefingFeed({ events: [] })).toEqual({ events: [] });
    expect(verifiedBriefingFeed({ events: [null] })).toBeNull();
    expect(verifiedBriefingFeed({ events: [{ id: 'event', title: 'Fixture', event_type: 'opportunity', action_type: 5 }] })).toBeNull();
    expect(verifiedBriefingApprovals({ drafts: [] })).toEqual({ drafts: [] });
    expect(verifiedBriefingApprovals({ drafts: [{ id: 'draft', status: 'approved' }] })).toBeNull();
  });

  it('rejects audit entries without trustworthy dates or structured metadata', () => {
    expect(verifiedBriefingAudit({ events: [{ id: 'audit', event_type: 'draft_approved',
      created_at: '2026-10-26T10:00:00Z', metadata: { execution_status: 'queued' },
    }] })?.events).toHaveLength(1);
    expect(verifiedBriefingAudit({ events: [{ id: 'audit', event_type: 'draft_approved', created_at: 'not-a-date' }] })).toBeNull();
    expect(verifiedBriefingAudit({ events: [{ id: 'audit', event_type: 'draft_approved',
      created_at: '2026-10-26T10:00:00Z', metadata: [],
    }] })).toBeNull();
  });

  it('prevents malformed sales chart records from crashing numeric formatting', () => {
    const chart = {
      total_revenue: 0, order_count: 0, avg_order_value: 0,
      vs_prior_period_pct: 0, top_account: '', chart_data: [],
    };
    expect(verifiedSalesChart(chart)).toEqual(chart);
    expect(verifiedSalesChart({ ...chart, total_revenue: '0' })).toBeNull();
    expect(verifiedSalesChart({ ...chart, chart_data: [{ date: 'invalid', revenue: 0, order_count: 0 }] })).toBeNull();
    expect(verifiedSalesChart({ ...chart, vs_prior_period_pct: Infinity })).toBeNull();
  });

  it('distinguishes an empty insights feed from malformed entries', () => {
    expect(verifiedDashboardFeed({ events: [] })).toEqual({ events: [] });
    expect(verifiedDashboardFeed({ events: [null] })).toBeNull();
    expect(verifiedDashboardFeed({ events: [{ id: 'test', title: 'Synthetic headline',
      event_type: 'opportunity', action_type: null, body: '', priority: 'unverified', is_read: false,
    }] })).toBeNull();
  });
});
