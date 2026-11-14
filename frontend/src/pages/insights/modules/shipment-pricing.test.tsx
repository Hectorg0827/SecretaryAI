import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../../lib/api';
import ShipmentPricing, { meta } from './shipment-pricing';

vi.mock('../../../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(<MemoryRouter><ShipmentPricing /></MemoryRouter>); });
  return container;
}

function write(name: string, value: string) {
  const element = container.querySelector<HTMLInputElement>(`[name="${name}"]`);
  if (!element) throw new Error(`Missing input ${name}`);
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  setter.call(element, value);
  element.dispatchEvent(new Event('input', { bubbles: true }));
}

beforeEach(() => {
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  container?.remove();
});

describe('shipment and warehouse costing', () => {
  it('registers as an owner/manager pricing module', () => {
    expect(meta.group).toBe('Pricing');
    expect([...meta.roles].sort()).toEqual(['manager', 'owner']);
  });

  it('refuses empty shipment and missing invoice evidence before API call', async () => {
    const page = await render();
    await act(async () => {
      page.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(api.post).not.toHaveBeenCalled();
    expect(page.textContent).toContain('Enter a shipment reference');
    await act(async () => {
      write('shipment_ref', 'PO-22');
    });
    expect(api.post).not.toHaveBeenCalled();
  });

  it('loads provisional approved physical receipts without treating costs as reconciled', async () => {
    vi.mocked(api.get).mockResolvedValue({
      assessment_id: '123e4567-e89b-12d3-a456-426614174000',
      po_number: 'PO-23',
      assertion_status: 'provisional',
      landed_cost_total: '125.00',
      currency: 'USD',
      lines: [
        { sku: 'TEST-1', unit_cost: '15', allocated_landed_cost: '100.00',
          cases: { available: '90', damaged: '10', quarantined: '0' } },
        { sku: 'TEST-2', unit_cost: '10', allocated_landed_cost: '25.00',
          cases: { available: '0', damaged: '0', quarantined: '10' } },
      ],
      valuation_verified: false,
      accounting_reconciled: false,
    });
    const page = await render();
    const input = page.querySelector<HTMLInputElement>('[aria-label="Purchasing assessment UUID"]')!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      setter.call(input, '123e4567-e89b-12d3-a456-426614174000');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const button = Array.from(page.querySelectorAll('button')).find((b) => b.textContent?.includes('Load source'))!;
    await act(async () => { button.click(); });
    expect(api.get).toHaveBeenCalledWith(
      '/api/logistics/finance/assessments/123e4567-e89b-12d3-a456-426614174000',
    );
    expect(page.textContent).toContain('provisional');
    expect(page.textContent).toContain('That amount is NOT added');
    expect(page.querySelectorAll('[aria-label^="Accepted cases SKU"]').length).toBe(1);
  });

  it('refuses a non-USD source rather than silently pricing foreign currency costs in USD', async () => {
    vi.mocked(api.get).mockResolvedValue({
      po_number: 'PO-24', assertion_status: 'provisional', landed_cost_total: '100.00',
      currency: 'EUR', lines: [{
        sku: 'EUR-1', unit_cost: '17', allocated_landed_cost: '5',
        cases: { available: '10', damaged: '0', quarantined: '0' },
      }],
    });
    const page = await render();
    const input = page.querySelector<HTMLInputElement>('[aria-label="Purchasing assessment UUID"]')!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      setter.call(input, '123e4567-e89b-12d3-a456-426614174000');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => {
      Array.from(page.querySelectorAll('button'))
        .find((b) => b.textContent?.includes('Load source'))!.click();
    });
    expect(page.textContent).toContain('Verify and document the FX conversion');
    expect(page.querySelector<HTMLInputElement>('[name="shipment_ref"]')?.value).toBe('');
  });

  it('renders calculated loaded cost with explicit incomplete-cost warning', async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 'analysis_only', expenses_total: '5.00',
      charges: [], costs_reconciled: false, prices_updated: false,
      missing_inbound_components: ['duty'],
      missing_warehouse_components: ['warehouse_outbound'],
      products: [{
        sku: 'TEST', accepted_cases: '10.000', inbound_cost_per_case: '15.000000',
        warehouse_cost_per_case: '0.500000', fully_loaded_cost_per_case: '15.500000',
        minimum_list_price_at_target_contribution: '23.15',
      }],
    });
    const page = await render();
    await act(async () => { write('shipment_ref', 'PO-22'); });
    const setByLabel = (label: string, value: string) => {
      const input = page.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      setter.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    };
    await act(async () => {
      setByLabel('SKU SKU 1', 'TEST');
      setByLabel('Accepted cases SKU 1', '10');
      setByLabel('Product cost/case SKU 1', '15');
      setByLabel('Total pallet-days SKU 1', '5');
      setByLabel('Invoice amount 1', '5.00');
      setByLabel('Document reference 1', 'WMS-Oct');
      setByLabel('Document revision 1', 'v1');
    });
    await act(async () => {
      page.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(api.post).toHaveBeenCalledWith('/api/insights/shipment-pricing/preview', expect.objectContaining({
      shipment_ref: 'PO-22', target_contribution_margin_pct: '30',
    }));
    expect(page.textContent).toContain('15.500000');
    expect(page.textContent).toContain('23.15');
    expect(page.textContent).toContain('Unknown costs are not evidence of zero expense.');
  });
});
