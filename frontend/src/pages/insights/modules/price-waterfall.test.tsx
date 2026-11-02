import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../../lib/api';
import PriceWaterfall, { meta } from './price-waterfall';

vi.mock('../../../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const response = {
  status: 'analysis_only', source: 'user_supplied', requires_human_review: true,
  actions_executed: false, generated_at: '2026-10-28T00:00:00Z',
  product_label: 'Valley Red', currency: 'USD',
  steps: [
    { key: 'list_price', label: 'List price', amount: 30, running: 30 },
    { key: 'discount', label: 'Discount', amount: -2, running: 28 },
    { key: 'returns_allowance', label: 'Returns allowance', amount: 0, running: 28 },
    { key: 'commission', label: 'Sales commission', amount: -1, running: 27 },
    { key: 'marketing', label: 'Marketing support', amount: -1, running: 26 },
    { key: 'landed_cost', label: 'Landed product cost', amount: -17, running: 9 },
  ],
  pocket_price_per_case: 26, landed_cost_per_case: 17, gross_margin_pct: 39.3,
  contribution_per_case: 9, contribution_margin_pct: 32.1,
  estimated_distributor_to_retailer: 37.33, estimated_retailer_to_consumer: 53.33,
  missing_cost_components: ['duty'],
  exception_reasons: ['missing_cost_categories', 'missing_approved_price_reference'],
};

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><PriceWaterfall /></MemoryRouter>);
  });
  return container;
}

function typeInto(page: HTMLElement, name: string, value: string) {
  const input = page.querySelector(`input[name="${name}"]`);
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  setter.call(input, value);
  input!.dispatchEvent(new Event('input', { bubbles: true }));
}

async function submit(page: HTMLElement) {
  await act(async () => {
    page.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

beforeEach(() => {
  vi.mocked(api.post).mockReset();
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
});

describe('price waterfall page', () => {
  it('is a pricing insight for roles with financial access', () => {
    expect([...meta.roles].sort()).toEqual(['manager', 'owner']);
    expect(meta.group).toBe('Pricing');
  });

  it('submits the entered price, deductions and costs, then shows the waterfall', async () => {
    vi.mocked(api.post).mockResolvedValue(response);
    const page = await render();
    await act(async () => {
      typeInto(page, 'product_label', 'Valley Red');
      typeInto(page, 'price_per_case', '30');
      typeInto(page, 'discount_per_case', '2');
      typeInto(page, 'commission_per_case', '1');
      typeInto(page, 'marketing_per_case', '1');
      typeInto(page, 'product', '16');
      typeInto(page, 'domestic_freight', '1');
    });
    await submit(page);
    expect(api.post).toHaveBeenCalledWith('/api/insights/price-waterfall/preview', {
      product_label: 'Valley Red', cost_evidence_reference: '', cost_evidence_version: '',
      price_per_case: 30, discount_per_case: 2, commission_per_case: 1, marketing_per_case: 1,
      target_margin_pct: 30, distributor_margin_pct: 25, retailer_margin_pct: 30,
      cost_lines: [
        { component: 'product', amount_per_case: 16 },
        { component: 'domestic_freight', amount_per_case: 1 },
      ],
    });
    const text = page.textContent ?? '';
    expect(text).toContain('Pocket price per case');
    expect(text).toContain('$26.00');
    expect(text).toContain('39.3%');
    expect(text).toContain('Some landed-cost items were left blank');
    expect(text).toContain('Not entered: Duty.');
  });

  it('asks for a list price before calling the server', async () => {
    const page = await render();
    await submit(page);
    expect(api.post).not.toHaveBeenCalled();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Enter the list price per case.');
  });

  it('shows the server validation message on failure', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('Discounts and allowances consume all net revenue'));
    const page = await render();
    await act(async () => { typeInto(page, 'price_per_case', '10'); });
    await submit(page);
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('consume all net revenue');
    expect(page.querySelector('section[aria-label="Waterfall result"]')).toBeNull();
  });
});
