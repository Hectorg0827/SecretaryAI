import { afterEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../../lib/api';
import ChurnRisk, { meta, type ChurnRiskResponse } from './churn-risk';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><ChurnRisk /></MemoryRouter>);
  });
  await act(async () => {});
  return container;
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
  vi.restoreAllMocks();
});

const RESPONSE: ChurnRiskResponse = {
  status: 'analysis_only',
  source: 'connected_data',
  requires_human_review: true,
  actions_executed: false,
  generated_at: '2026-10-28T12:00:00Z',
  as_of: '2026-10-28',
  customers_assessed: 7,
  counts: { high: 1, medium: 1, low: 1, unknown: 3, not_applicable: 1 },
  at_risk: [
    {
      customer_id: 'QB2', name: 'Bayside Grocers', risk: 'high', score: 0.85,
      why: 'No order for 220 days; they usually order about every 20 days.',
      next_step: 'Call or visit this week: ask how things are going and whether anything has changed.',
      last_order_date: '2026-03-22', orders_last_12_months: 3, history_complete: true,
    },
    {
      customer_id: 'QB3', name: 'Corner Deli', risk: 'medium', score: 0.6,
      why: 'Ordering has slowed: last order 60 days ago and 3 orders in the last 12 months.',
      next_step: 'Check in soon with a friendly call, a visit or a note about what is new.',
      last_order_date: '2026-08-29', orders_last_12_months: 3, history_complete: true,
    },
  ],
  insufficient_data: [
    {
      customer_id: 'QB6', name: 'Fjord Supply', risk: 'unknown', score: null,
      why: "Some of this customer's orders could not be read, so their history is incomplete.",
      next_step: 'Not enough order history to judge; review this account yourself.',
      last_order_date: '2026-10-08', orders_last_12_months: 2, history_complete: false,
    },
  ],
  truncated: false,
  rows_skipped: 4,
  rows_unattributed: 1,
  customers_skipped: 0,
  method: 'Rule-based screen of how recently and how often each customer orders.',
};

describe('churn-risk insight', () => {
  it('is offered only to roles the backend admits (view_customers)', () => {
    expect([...meta.roles].sort()).toEqual(['back_office', 'manager', 'owner']);
    expect(meta.group).toBe('Customers');
  });

  it('shows at-risk customers with the reason and a suggested next step', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue(RESPONSE);
    const page = await render();

    expect(get).toHaveBeenCalledWith('/api/insights/churn-risk');
    const text = page.textContent ?? '';
    expect(text).toContain('Analysis only.');
    expect(text).toContain('Bayside Grocers');
    expect(text).toContain('High risk');
    expect(text).toContain('No order for 220 days; they usually order about every 20 days.');
    expect(text).toContain('Call or visit this week');
    expect(text).toContain('Corner Deli');
    expect(text).toContain('Slowing down');
    expect(text).toContain('Not enough history to judge');
    expect(text).toContain('Fjord Supply');
    expect(text).toContain('4 order(s) could not be read');
    expect(text).toContain('1 order(s) had no customer attached');
    const rows = page.querySelectorAll('section[aria-label="Customers to check on"] tbody tr');
    expect(rows).toHaveLength(2);
  });

  it('shows an error instead of an empty list when the source is down', async () => {
    vi.spyOn(api, 'get').mockRejectedValue(new Error('Customer list is unavailable; no analysis was produced'));
    const page = await render();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Customer list is unavailable');
    expect(page.textContent).not.toContain('No customers look at risk');
  });
});
