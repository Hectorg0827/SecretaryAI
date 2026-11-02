import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../../lib/api';
import SupplierTcoInsight, { meta } from './supplier-tco';

vi.mock('../../../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));

const comparison = {
  status: 'analysis_only', source: 'user_supplied', requires_human_review: true, actions_executed: false,
  generated_at: '2026-10-28T12:00:00Z',
  recommended_supplier_id: 'Caribe Foods',
  annual_savings_vs_current: 5702.7,
  rationale: 'Candidate switch to Caribe Foods models $5,703/year savings.',
  supplier_comparison: [
    { supplier_id: 'Caribe Foods', cost_per_unit: { total: 12.1414, purchase_price: 8.5 }, total_annual_tco: 121413.7, non_purchase_cost_pct: 30 },
    { supplier_id: 'Andes Bottling', cost_per_unit: { total: 12.7116, purchase_price: 10 }, total_annual_tco: 127116.4, non_purchase_cost_pct: 21.3 },
  ],
  esg_scores: [{
    entity_id: 'Andes Bottling', scores: { overall: 78.1, environmental: 70.2, social: 87.5, governance: 96 },
    improvement_opportunities: ['Only 60% recyclable packaging — target 80%+ for major retailer compliance'],
  }],
};

const COSTS: Record<string, string> = {
  purchase_price: '10', inbound_freight_per_unit: '1', customs_duty_rate_pct: '5', brokerage_per_unit: '0.2',
  days_inventory_outstanding: '60', return_rate_pct: '2', return_handling_cost: '5',
  replacement_cost_per_return: '10', kg_co2e_per_unit: '2',
};

const ESG: Record<string, string> = {
  carbon_per_case: '1', packaging_waste_kg_per_unit: '0.3', recyclable_packaging_pct: '60', water_intensity: '2',
  safety_incidents: '0', supplier_audit_pass_rate: '90', local_sourcing_pct: '40', on_time_payment_rate: '95',
  import_compliance_rate: '97',
};

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><SupplierTcoInsight /></MemoryRouter>);
  });
  return container;
}

function setValue(page: HTMLElement, name: string, value: string) {
  const field = page.querySelector(`input[name="${name}"]`) as HTMLInputElement;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(field, value);
  field.dispatchEvent(new Event('input', { bubbles: true }));
}

async function fillSupplier(page: HTMLElement, index: number, name: string, costs: Record<string, string>) {
  await act(async () => {
    setValue(page, `supplier-${index}-name`, name);
    for (const [field, value] of Object.entries(costs)) setValue(page, `supplier-${index}-${field}`, value);
  });
}

async function submit(page: HTMLElement) {
  await act(async () => {
    page.querySelector('form[aria-label="Supplier comparison"]')
      ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
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

describe('supplier TCO insight', () => {
  it('is offered only to roles the backend view_financials check admits', () => {
    expect([...meta.roles].sort()).toEqual(['manager', 'owner']);
    expect(meta.group).toBe('Supply chain');
  });

  it('submits every supplier cost, the current supplier and optional ESG scores', async () => {
    vi.mocked(api.post).mockResolvedValue(comparison);
    const page = await render();
    expect(page.querySelector('[role="note"]')?.textContent).toContain('Calculated from the values you entered.');
    await act(async () => {
      setValue(page, 'product', 'Malta 24x12oz');
      setValue(page, 'annual_volume', '10000');
    });
    await fillSupplier(page, 0, 'Andes Bottling', COSTS);
    await fillSupplier(page, 1, 'Caribe Foods', { ...COSTS, purchase_price: '8.5', volume_rebate_pct: '2' });
    await act(async () => {
      (page.querySelector('input[name="supplier-0-with-esg"]') as HTMLInputElement).click();
    });
    await act(async () => {
      for (const [field, value] of Object.entries(ESG)) setValue(page, `supplier-0-esg-${field}`, value);
    });
    await submit(page);

    const numeric = (costs: Record<string, string>) =>
      Object.fromEntries(Object.entries(costs).map(([k, v]) => [k, Number(v)]));
    expect(api.post).toHaveBeenCalledWith('/api/insights/supplier-tco/preview', {
      product: 'Malta 24x12oz',
      annual_volume: 10000,
      unit_of_measure: 'case',
      currency: 'USD',
      wacc_pct: 10,
      current_supplier: 'Andes Bottling',
      suppliers: [
        { name: 'Andes Bottling', ...numeric(COSTS), holding_cost_rate_pct: 25, volume_rebate_pct: 0, esg: numeric(ESG) },
        { name: 'Caribe Foods', ...numeric(COSTS), purchase_price: 8.5, holding_cost_rate_pct: 25, volume_rebate_pct: 2 },
      ],
    });
    const result = page.querySelector('[data-testid="tco-result"]')?.textContent ?? '';
    expect(result).toContain('Lowest all-in cost: Caribe Foods');
    expect(result).toContain('$5,703 a year less');
    expect(result).toContain('$12.1414');
    expect(result).toContain('Andes Bottling: overall 78.1');
  });

  it('does not submit when a cost is missing', async () => {
    const page = await render();
    await act(async () => {
      setValue(page, 'product', 'Malta');
      setValue(page, 'annual_volume', '100');
    });
    await fillSupplier(page, 0, 'Andes Bottling', COSTS);
    await submit(page);
    expect(api.post).not.toHaveBeenCalled();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('every cost for each supplier');
  });

  it('shows the server validation error', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('Supplier names must be unique, include the current supplier, and every cost must be filled in'));
    const page = await render();
    await act(async () => {
      setValue(page, 'product', 'Malta');
      setValue(page, 'annual_volume', '100');
    });
    await fillSupplier(page, 0, 'Same', COSTS);
    await fillSupplier(page, 1, 'Same', COSTS);
    await submit(page);
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Supplier names must be unique');
    expect(page.querySelector('[data-testid="tco-result"]')).toBeNull();
  });
});
