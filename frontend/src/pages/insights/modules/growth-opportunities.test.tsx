import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../../lib/api';
import GrowthOpportunities, { meta } from './growth-opportunities';

vi.mock('../../../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const signal = (overrides: Record<string, unknown>) => ({
  department: 'brand', entity_ref: 'ITEM-A', product_name: 'Valley Red 12x750ml', sku: 'VR-12',
  status: 'needs_review', evidence_quality: 'verified', ...overrides,
});

const response = {
  status: 'analysis_only', source: 'connected_data', requires_human_review: true,
  actions_executed: false, generated_at: '2026-10-28T00:00:00Z',
  weeks: 4,
  current_period: { start: '2026-10-01', end: '2026-10-28' },
  thresholds: { change_pct: 20, min_margin_pct: 15 },
  signals_total: 3,
  rows_skipped: 0,
  sections: {
    red_alerts: [signal({
      signal_code: 'portfolio_stockout_with_demand', entity_ref: 'ITEM-B', product_name: 'Coast White',
      status: 'source_unavailable', evidence_quality: 'unavailable', display_metric: null,
      next_action_code: 'verify_source_evidence',
    })],
    yellow_alerts: [signal({
      signal_code: 'brand_decline', entity_ref: 'ITEM-B', product_name: 'Coast White',
      display_metric: -50, next_action_code: 'review_brand_vitality',
    })],
    opportunities: [],
    scoreboard: [signal({ signal_code: 'brand_growth', display_metric: 26.7, next_action_code: 'review_growth_evidence' })],
  },
};

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><GrowthOpportunities /></MemoryRouter>);
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

describe('growth opportunities page', () => {
  it('is offered to the roles the backend report permission admits', () => {
    expect([...meta.roles].sort()).toEqual(['back_office', 'manager', 'owner']);
  });

  it('groups signals by urgency with plain-language next steps', async () => {
    vi.mocked(api.get).mockResolvedValue(response);
    const page = await render();
    expect(api.get).toHaveBeenCalledWith('/api/insights/growth-opportunities?weeks=4');
    const sections = [...page.querySelectorAll('section')].map((s) => s.getAttribute('aria-label'));
    expect(sections).toEqual(['Needs attention now', 'Keep an eye on', 'Going well']);
    const text = page.textContent ?? '';
    expect(text).toContain('Out of stock while customers still order');
    expect(text).toContain('Source needs checking');
    expect(text).toContain('-50.0% cases vs the period before');
    expect(text).toContain('+26.7% cases vs the period before');
    expect(text).toContain('Review the product scorecard');
  });

  it('shows an error state when the connected source fails', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Inventory data is unavailable; no analysis was produced'));
    const page = await render();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Inventory data is unavailable');
    expect(page.querySelectorAll('section')).toHaveLength(0);
  });
});
