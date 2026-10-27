import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DashboardSummary } from '../../lib/api';

const mocks = vi.hoisted(() => ({
  sales: vi.fn(), notes: vi.fn(), feed: vi.fn(), pending: vi.fn(), audit: vi.fn(),
}));

vi.mock('../../lib/api', () => ({
  api: {
    dashboard: { sales: mocks.sales, notes: mocks.notes },
    feed: { list: mocks.feed },
    actions: { pending: mocks.pending },
    get: mocks.audit,
  },
}));

import { MorningBriefing } from './MorningBriefing';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

const summary: DashboardSummary = {
  accounts: { healthy: 5, slowing: 2, at_risk: 1, dormant: 0 },
  inventory_alerts: [{ item_id: 'fixture-stock', product_name: 'Synthetic stock', total_qty: 0, weeks_remaining: 0, stock_status: 'out_of_stock', needs_po: false }],
  pending_actions: 1,
  unread_emails: 0,
  generated_at: '2026-10-26T12:00:00Z',
  cached: true,
};

async function render(summaryValue: DashboardSummary | null) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><MorningBriefing summary={summaryValue} summaryLoading={false} /></MemoryRouter>);
  });
  await act(async () => { await Promise.resolve(); });
  return container;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.sales.mockResolvedValue({ total_revenue: 1234, order_count: 4 });
  mocks.notes.mockResolvedValue({ notes: [
    { id: 'due', text: 'Synthetic decision', due_date: '2020-01-01', done: false },
    { id: 'done', text: 'Synthetic completed', due_date: '2020-01-01', done: true },
  ] });
  mocks.feed.mockResolvedValue({ events: [
    { id: 'insight', event_type: 'insight', action_type: null, title: 'Ordinary headline' },
    { id: 'opportunity', event_type: 'opportunity', action_type: null, title: 'Synthetic opportunity' },
  ] });
  mocks.pending.mockResolvedValue({ drafts: [{ id: 'fixture-pending' }] });
  mocks.audit.mockResolvedValue({ events: [{
    id: 'audit-1', event_type: 'workflow_failed', created_at: '2026-10-26T12:30:00Z',
  }] });
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
});

describe('six-section owner briefing (synthetic API-only)', () => {
  it('renders six separately sourced decisions and never treats approval as delivery', async () => {
    const node = await render(summary);
    expect(node.querySelectorAll('section[aria-label]')).toHaveLength(6);
    const text = node.textContent ?? '';
    expect(text).toContain('1 at-risk accounts');
    expect(text).toContain('1 critical or out-of-stock inventory alerts');
    expect(text).toContain('2 slowing accounts');
    expect(text).toContain('Synthetic opportunity');
    expect(text).not.toContain('Ordinary headline');
    expect(text).toContain('$1,234.00');
    expect(text).toContain('1 drafts waiting for human approval');
    expect(text).toContain('1 unfinished follow-ups');
    expect(text).toContain('workflow failed · execution not confirmed');
    expect(text).toContain('cached');
    expect(mocks.audit).toHaveBeenCalledWith('/api/admin/audit-log?page=1&page_size=25');
  });

  it('displays source uncertainty instead of inventing zero revenue or healthy stock', async () => {
    mocks.sales.mockRejectedValue(new Error('Synthetic sales outage'));
    mocks.audit.mockRejectedValue(new Error('Synthetic audit outage'));
    const node = await render(null);
    const scoreboard = node.querySelector('[aria-label="4 · Scoreboard"]');
    expect(scoreboard?.textContent).toContain('Source unavailable. No conclusion can be drawn.');
    expect(scoreboard?.textContent).not.toContain('$0.00');
    expect(node.querySelector('[aria-label="1 · Red flags"]')?.textContent).toContain('Source unavailable');
    expect(node.querySelector('[aria-label="6 · Actions & failures"]')?.textContent).toContain('Source unavailable');
    expect(node.textContent).not.toContain('0 critical or out-of-stock inventory alerts');
  });

  it('rejects malformed financial evidence without exposing a fabricated scoreboard', async () => {
    mocks.sales.mockResolvedValue({ total_revenue: '0', order_count: 0 });
    const node = await render(summary);
    expect(node.querySelector('[aria-label="4 · Scoreboard"]')?.textContent).toContain('Source unavailable');
    expect(node.querySelector('[aria-label="4 · Scoreboard"]')?.textContent).not.toContain('$0.00');
  });
});
