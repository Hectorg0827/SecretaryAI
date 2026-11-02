import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../../lib/api';
import BrandScorecards, { meta } from './brand-scorecards';

vi.mock('../../../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const advisory = {
  status: 'analysis_only', requires_human_review: true, actions_executed: false,
  generated_at: '2026-10-28T00:00:00Z',
};

const scorecards = {
  ...advisory,
  source: 'connected_data',
  weeks: 4,
  current_period: { start: '2026-10-01', end: '2026-10-28' },
  previous_period: { start: '2026-09-03', end: '2026-09-30' },
  products_total: 2,
  rows_skipped: 1,
  products: [
    {
      item_id: 'ITEM-A', name: 'Valley Red 12x750ml', sku: 'VR-12', net_sales: 1140, net_cases: 38,
      net_case_growth_pct: 26.7, account_penetration_pct: 66.7, repeat_account_pct: 50,
      return_rate_pct: 5, gross_margin_pct: 33.3, weeks_of_supply: 6.3, gaps: [],
      review_status: 'draft_requires_review',
    },
    {
      item_id: 'ITEM-C', name: 'Gift Box', sku: 'GB-1', net_sales: 100, net_cases: 5,
      net_case_growth_pct: null, account_penetration_pct: 33.3, repeat_account_pct: 0,
      return_rate_pct: 0, gross_margin_pct: null, weeks_of_supply: 8, gaps: ['unavailable_product_cost'],
      review_status: 'draft_requires_review',
    },
  ],
};

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><BrandScorecards /></MemoryRouter>);
  });
  return container;
}

function typeInto(input: Element | null, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  setter.call(input, value);
  input!.dispatchEvent(new Event('input', { bubbles: true }));
}

beforeEach(() => {
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
});

describe('brand scorecards page', () => {
  it('is offered only to roles with financial access', () => {
    expect([...meta.roles].sort()).toEqual(['manager', 'owner']);
    expect(meta.group).toBe('Growth');
  });

  it('renders each product scorecard from connected data', async () => {
    vi.mocked(api.get).mockResolvedValue(scorecards);
    const page = await render();
    expect(api.get).toHaveBeenCalledWith('/api/insights/brand-scorecards?weeks=4');
    const text = page.textContent ?? '';
    expect(text).toContain('Valley Red 12x750ml');
    expect(text).toContain('+26.7%');
    expect(text).toContain('66.7%');
    expect(text).toContain('33.3%');
    expect(text).toContain('Product cost missing in QuickBooks');
    expect(text).toContain('1 record(s) could not be read');
    expect(page.querySelector('[role="note"]')?.textContent).toContain('Analysis only');
  });

  it('shows an error instead of empty numbers when the source is down', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Sales history is unavailable; no analysis was produced'));
    const page = await render();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Sales history is unavailable');
    expect(page.querySelector('table')).toBeNull();
  });

  it('submits entered supplier figures and shows the scores', async () => {
    vi.mocked(api.get).mockResolvedValue(scorecards);
    vi.mocked(api.post).mockResolvedValue({
      ...advisory, source: 'user_supplied',
      suppliers: [{
        supplier_name: 'Atlantic Imports', fill_rate_pct: 90, on_time_pct: 80, damage_rate_pct: 2,
        composite_score: 87.6, documented_inputs: true, gaps: [],
      }],
    });
    const page = await render();
    const values: Record<string, string> = {
      supplier_name_0: 'Atlantic Imports', units_ordered_0: '1000', units_filled_0: '900',
      units_delivered_0: '900', units_damaged_0: '18', on_time_shipments_0: '8',
      received_shipments_0: '10', evidence_reference_0: 'Receiving log', evidence_version_0: 'Oct 2026',
    };
    await act(async () => {
      for (const [name, value] of Object.entries(values)) typeInto(page.querySelector(`input[name="${name}"]`), value);
    });
    await act(async () => {
      page.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(api.post).toHaveBeenCalledWith('/api/insights/brand-scorecards/supplier-preview', {
      suppliers: [{
        supplier_name: 'Atlantic Imports', units_ordered: 1000, units_filled: 900, units_delivered: 900,
        units_damaged: 18, on_time_shipments: 8, received_shipments: 10,
        evidence_reference: 'Receiving log', evidence_version: 'Oct 2026',
      }],
    });
    expect(page.textContent).toContain('87.6 / 100');
  });

  it('does not submit incomplete supplier figures', async () => {
    vi.mocked(api.get).mockResolvedValue(scorecards);
    const page = await render();
    await act(async () => {
      page.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(api.post).not.toHaveBeenCalled();
    expect(page.textContent).toContain('Fill in every number for each supplier.');
  });
});
