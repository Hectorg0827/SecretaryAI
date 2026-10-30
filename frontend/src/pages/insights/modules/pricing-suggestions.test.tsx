import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../../lib/api';
import PricingSuggestionsInsight, { meta } from './pricing-suggestions';

vi.mock('../../../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));

const advisory = {
  status: 'analysis_only', requires_human_review: true, actions_executed: false,
  generated_at: '2026-10-28T12:00:00Z',
};

const suggestion = {
  current_price: 10, suggested_price: 10.6122, change_pct: 6.12, current_margin_pct: 20,
  projected_margin_pct: 24.62, below_target_margin: true, volume_impact_pct: -14.3,
  revenue_impact_annual: 300, rationale: 'Current margin 20.0% is below target 30.0%.',
  price_changes_observed: 12, price_sensitivity: 'highly_elastic',
};

const overview = {
  ...advisory,
  source: 'connected_data',
  target_margin_pct: 30,
  items: [
    { item_id: 'QB-1', product_name: 'Malta', sku: 'MALT-1', status: 'suggested_change', reason: '', ...suggestion },
    {
      item_id: 'QB-2', product_name: 'Ginger Beer', sku: 'GB-1', status: 'hold', reason: '', ...suggestion,
      suggested_price: 20, current_price: 20, change_pct: 0, below_target_margin: false, current_margin_pct: 50,
      price_changes_observed: 0, price_sensitivity: 'unknown', rationale: 'Insufficient evidence; hold price for human review.',
    },
    { item_id: 'QB-4', product_name: 'Coconut Water', sku: 'CW-1', status: 'insufficient_data', reason: 'No sales in the last 12 months.' },
  ],
  summary: { items_total: 3, suggested_changes: 1, holds: 1, below_target_margin: 1, items_insufficient_data: 1, rows_skipped: 0 },
};

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><PricingSuggestionsInsight /></MemoryRouter>);
  });
  return container;
}

function setValue(el: Element | null, value: string) {
  const field = el as HTMLInputElement | HTMLSelectElement;
  const proto = field instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value')?.set?.call(field, value);
  field.dispatchEvent(new Event(field instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
}

async function submit(page: HTMLElement, section: string) {
  await act(async () => {
    page.querySelector(`section[aria-label="${section}"] form`)
      ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
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

describe('pricing suggestions insight', () => {
  it('is a Pricing insight for roles the backend view_financials check admits', () => {
    expect([...meta.roles].sort()).toEqual(['manager', 'owner']);
    expect(meta.group).toBe('Pricing');
  });

  it('renders suggestions, holds and insufficient-data rows', async () => {
    vi.mocked(api.get).mockResolvedValue(overview);
    const page = await render();
    expect(api.get).toHaveBeenCalledWith('/api/insights/pricing-suggestions?target_margin_pct=30');
    const text = page.querySelector('section[aria-label="Suggestions from your sales"]')?.textContent ?? '';
    expect(text).toContain('$10.61');
    expect(text).toContain('+6.1%');
    expect(text).toContain('below target');
    expect(text).toContain('Very price-sensitive');
    expect(text).toContain('Keep current price');
    expect(text).toContain('No sales in the last 12 months.');
  });

  it('shows the outage instead of an empty table', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Sales history is unavailable; no analysis was produced'));
    const page = await render();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Sales history is unavailable');
    expect(page.querySelector('section[aria-label="Suggestions from your sales"]')).toBeNull();
  });

  it('submits a price check with entered history', async () => {
    vi.mocked(api.get).mockResolvedValue(overview);
    vi.mocked(api.post).mockResolvedValue({ ...advisory, ...suggestion, source: 'user_supplied', product_name: 'Malta', outcome: 'suggested_change' });
    const page = await render();
    await act(async () => {
      setValue(page.querySelector('input[name="product_name"]'), 'Malta');
      setValue(page.querySelector('input[name="current_price"]'), '10');
      setValue(page.querySelector('input[name="unit_cost"]'), '8');
      setValue(page.querySelector('input[name="annual_units"]'), '1000');
      setValue(page.querySelector('input[name="competitor_prices"]'), '9.5, 10.5');
      setValue(page.querySelector('input[name="change-0-old"]'), '10');
      setValue(page.querySelector('input[name="change-0-new"]'), '11');
      setValue(page.querySelector('input[name="change-0-before"]'), '100');
      setValue(page.querySelector('input[name="change-0-after"]'), '80');
    });
    await submit(page, 'Check a price');
    expect(api.post).toHaveBeenCalledWith('/api/insights/pricing-suggestions/preview', {
      product_name: 'Malta', current_price: 10, unit_cost: 8, annual_units: 1000, target_margin_pct: 30,
      competitor_prices: [9.5, 10.5],
      price_changes: [{ old_price: 10, new_price: 11, units_before: 100, units_after: 80, period_days: 30 }],
    });
    const result = page.querySelector('[data-testid="price-check-result"]')?.textContent ?? '';
    expect(result).toContain('Calculated from the values you entered.');
    expect(result).toContain('Suggested price $10.61');
  });

  it('submits a supplier increase plan for the filled customer groups only', async () => {
    vi.mocked(api.get).mockResolvedValue(overview);
    vi.mocked(api.post).mockResolvedValue({
      ...advisory, source: 'user_supplied', supplier_increase_pct: 20, blended_pass_through_pct: 76.7,
      recommended_summary: 'Supplier increased cost by 20.0%.',
      segment_recommendations: [
        { segment: 'Key accounts', recommended_price: 10.7, price_change_pct: 7, projected_margin_pct: 43.9,
          pass_through_strategy: 'staged', staged_timeline_days: 90 },
      ],
    });
    const page = await render();
    await act(async () => {
      setValue(page.querySelector('input[name="supplier"]'), 'Andes Bottling');
      setValue(page.querySelector('input[name="increase_product_name"]'), 'Malta');
      setValue(page.querySelector('input[name="cost_before"]'), '5');
      setValue(page.querySelector('input[name="cost_after"]'), '6');
      setValue(page.querySelector('input[name="increase_current_price"]'), '10');
      setValue(page.querySelector('input[name="tier_1-units"]'), '1000');
      setValue(page.querySelector('input[name="tier_3-units"]'), '500');
      setValue(page.querySelector('select[name="tier_3-ped"]'), '-0.3');
    });
    await submit(page, 'Supplier raised costs');
    expect(api.post).toHaveBeenCalledWith('/api/insights/pricing-suggestions/supplier-increase', {
      supplier: 'Andes Bottling', product_name: 'Malta', cost_before: 5, cost_after: 6, current_price: 10,
      segments: [
        { segment: 'Key accounts', pricing_tier: 'tier_1', annual_units: 1000 },
        { segment: 'Small shops', pricing_tier: 'tier_3', annual_units: 500, elasticity_ped: -0.3 },
      ],
    });
    const result = page.querySelector('[data-testid="increase-result"]')?.textContent ?? '';
    expect(result).toContain('Pass it on in steps over 90 days');
    expect(result).toContain('$10.70');
  });
});
