import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../../lib/api';
import PortfolioReview, { meta } from './portfolio-review';

vi.mock('../../../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const row = (overrides: Record<string, unknown>) => ({
  sku: '', status: 'maintain_or_invest_candidate', reasons: [], net_sales: 0, net_cases: 0,
  contribution_margin_pct: null, weeks_of_supply: null, working_capital_tied: null,
  no_sales_in_period: false, ...overrides,
});

const response = {
  status: 'analysis_only', source: 'connected_data', requires_human_review: true,
  actions_executed: false, generated_at: '2026-10-28T00:00:00Z',
  weeks: 13,
  period: { start: '2026-07-30', end: '2026-10-28' },
  policy: { min_margin_pct: 15, max_weeks_of_supply: 26 },
  summary: { review_phase_out: 1, fix: 0, check_data: 0, keep: 1 },
  working_capital_in_phase_out_review: 2000,
  rows_skipped: 0,
  checks_not_applied: ['aged_inventory', 'product_dependencies'],
  products: [
    row({
      item_id: 'ITEM-E', name: 'Slow Sparkling', decision: 'review_phase_out',
      status: 'rationalization_candidate_for_review',
      reasons: ['low_contribution_margin', 'excess_weeks_of_supply'], net_sales: 140,
      contribution_margin_pct: 7.1, weeks_of_supply: 200, working_capital_tied: 2000,
    }),
    row({
      item_id: 'ITEM-A', name: 'Valley Red', decision: 'keep', net_sales: 2040,
      contribution_margin_pct: 33.3, weeks_of_supply: 11.5, working_capital_tied: 1200,
    }),
  ],
};

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><PortfolioReview /></MemoryRouter>);
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

describe('product range review page', () => {
  it('is offered only to roles with financial access', () => {
    expect([...meta.roles].sort()).toEqual(['manager', 'owner']);
  });

  it('shows keep, fix and phase-out suggestions with reasons', async () => {
    vi.mocked(api.get).mockResolvedValue(response);
    const page = await render();
    expect(api.get).toHaveBeenCalledWith('/api/insights/portfolio-review?weeks=13');
    const text = page.textContent ?? '';
    expect(text).toContain('Review: reprice or phase out');
    expect(text).toContain('Margin below your target; Far more stock than recent sales need');
    expect(text).toContain('7.1%');
    expect(text).toContain('$2,000.00');
    expect(text).toContain('Healthy margin and stock cover');
    expect(page.querySelectorAll('tbody tr')).toHaveLength(2);
  });

  it('shows an error state when the connected source fails', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Sales history is unavailable; no analysis was produced'));
    const page = await render();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Sales history is unavailable');
    expect(page.querySelector('table')).toBeNull();
  });
});
