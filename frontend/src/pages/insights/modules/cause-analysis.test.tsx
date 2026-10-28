import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../../lib/api';
import CauseAnalysisInsight, { meta } from './cause-analysis';

vi.mock('../../../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));

const analysis = (root_cause: string, evidence: string[], alternatives: string[] = []) => ({
  anomaly_description: '', segment_reasoning: 'A specific, addressable issue is driving the silence.',
  customer_segment: root_cause === 'pricing_pressure' ? 'persuadable' : null, data_quality_warning: '',
  primary_cause: { root_cause, probability: 0.7, evidence, intervention: 'Review pricing with the buyer.' },
  alternative_causes: alternatives.map((code) => ({ root_cause: code, probability: 0.3, evidence: [], intervention: '' })),
});

const response = {
  status: 'analysis_only', source: 'connected_data', requires_human_review: true, actions_executed: false,
  generated_at: '2026-10-28T12:00:00Z',
  customer_drops: [{
    customer_id: 'C1', customer_name: 'Bodega Uno', trigger: 'No order for 60 days (usually every 14 days)',
    revenue_last_90_days: 440, revenue_prior_90_days: 1320,
    analysis: analysis('pricing_pressure', ['Average price increase of 10.0% across 1 SKUs'], ['seasonal_pattern']),
  }],
  stock_risks: [{
    item_id: 'QB-1', product_name: 'Malta', sku: 'MALT-1', days_of_stock_left: 4, avg_daily_sales: 5,
    typical_supplier_days: 10, late_purchase_orders: 1,
    analysis: analysis('supply_disruption', ['1 recent supplier delays']),
  }],
  summary: {
    customers_reviewed: 4, customers_flagged: 1, customers_with_too_little_history: 1,
    items_with_recent_sales: 3, items_flagged: 1, rows_skipped: 0,
  },
  evidence_gaps: ['Competitor activity is not tracked.'],
};

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><CauseAnalysisInsight /></MemoryRouter>);
  });
  return container;
}

beforeEach(() => {
  vi.mocked(api.get).mockReset();
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
});

describe('cause analysis insight', () => {
  it('is offered only to roles the backend view_customers check admits', () => {
    expect([...meta.roles].sort()).toEqual(['back_office', 'manager', 'owner']);
  });

  it('renders ranked causes in plain language', async () => {
    vi.mocked(api.get).mockResolvedValue(response);
    const page = await render();
    expect(api.get).toHaveBeenCalledWith('/api/insights/cause-analysis');
    const customers = page.querySelector('section[aria-label="Customers buying less"]')?.textContent ?? '';
    expect(customers).toContain('Bodega Uno');
    expect(customers).toContain('No order for 60 days');
    expect(customers).toContain('Price increase or competitor pricing');
    expect(customers).toContain('Average price increase of 10.0%');
    expect(customers).toContain('Other possible causes: Normal seasonal slowdown');
    expect(customers).toContain('Worth a personal follow-up');
    const stock = page.querySelector('section[aria-label="Products running short"]')?.textContent ?? '';
    expect(stock).toContain('Malta');
    expect(stock).toContain('Supplier delay or shortage');
    expect(stock).toContain('1 purchase order(s) running late');
    expect(page.textContent).toContain('Competitor activity is not tracked.');
  });

  it('shows the outage instead of an empty analysis', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Sales history is unavailable; no analysis was produced'));
    const page = await render();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Sales history is unavailable');
    expect(page.querySelector('section[aria-label="Customers buying less"]')).toBeNull();
  });
});
