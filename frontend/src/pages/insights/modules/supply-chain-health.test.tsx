import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../../lib/api';
import SupplyChainHealthInsight, { meta } from './supply-chain-health';

vi.mock('../../../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));

const advisory = {
  status: 'analysis_only', requires_human_review: true, actions_executed: false,
  generated_at: '2026-10-28T12:00:00Z',
};

const measured = {
  ...advisory,
  source: 'connected_data',
  measured: {
    days_inventory_outstanding: 97.3, days_sales_outstanding: 73,
    supplier_lead_time_days: 12, supplier_lead_time_observations: 2,
  },
  needs_input: [
    { metric: 'days_payable_outstanding', label: 'Days to pay suppliers', why: 'Needs your accounts payable balance.' },
  ],
  prefill: {
    revenue: 10000, cogs: 3000, inventory_value: 800, ar_balance: 2000, supplier_lead_times_days: [10, 14],
  },
  coverage: { cogs_revenue_coverage_pct: 100, inventory_items_missing_cost: 1 },
  summary: { rows_skipped: 0 },
};

const check = {
  ...advisory,
  source: 'user_supplied',
  scor_metrics: {
    responsiveness: { supplier_lead_time_days: 12 },
    cost: { supply_chain_cost_pct_revenue: 9 },
    assets: {
      cash_to_cash_cycle_days: 50.1, days_inventory_outstanding: 52.1, days_sales_outstanding: 29.2,
      days_payable_outstanding: 31.3, return_on_working_capital_pct: 175,
    },
  },
  benchmarks: { cash_to_cash: 'fair', supply_chain_cost: 'good', perfect_order_fulfillment: 'insufficient_data' },
  primary_constraint: {
    node_name: 'Warehouse picking', constraint_type: 'warehouse_throughput', severity: 'high',
    utilization_pct: 95, queue_depth: 40, exploit_actions: ['Batch pick orders by zone to reduce travel time'],
    subordinate_actions: [], elevate_actions: ['Add weekend shift or evening shift during peak periods'],
  },
  all_constraints: [],
  improvement_priority: ['[CONSTRAINT] Resolve Warehouse picking bottleneck (high severity, 40% gain potential)'],
};

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><SupplyChainHealthInsight /></MemoryRouter>);
  });
  return container;
}

function setValue(el: Element | null, value: string) {
  const field = el as HTMLInputElement | HTMLSelectElement;
  const proto = field instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value')?.set?.call(field, value);
  field.dispatchEvent(new Event(field instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
}

async function submit(page: HTMLElement) {
  await act(async () => {
    page.querySelector('section[aria-label="Full health check"] form')
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

describe('supply chain health insight', () => {
  it('is offered only to roles the backend view_financials check admits', () => {
    expect([...meta.roles].sort()).toEqual(['manager', 'owner']);
  });

  it('shows measured metrics and what still needs input', async () => {
    vi.mocked(api.get).mockResolvedValue(measured);
    const page = await render();
    expect(api.get).toHaveBeenCalledWith('/api/insights/supply-chain-health');
    const section = page.querySelector('section[aria-label="Measured from your data"]')?.textContent ?? '';
    expect(section).toContain('97.3 days');
    expect(section).toContain('73 days');
    expect(section).toContain('12 days');
    expect(section).toContain('Days to pay suppliers');
    expect((page.querySelector('input[name="revenue"]') as HTMLInputElement).value).toBe('10000');
    expect((page.querySelector('input[name="ap_balance"]') as HTMLInputElement).value).toBe('');
    expect((page.querySelector('input[name="supplier_lead_times_days"]') as HTMLInputElement).value).toBe('10, 14');
  });

  it('shows an error state when connected data is unavailable', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Customer balances is unavailable; no analysis was produced'));
    const page = await render();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Customer balances is unavailable');
    expect(page.querySelector('section[aria-label="Measured from your data"]')).toBeNull();
  });

  it('submits confirmed figures and process steps, then shows the bottleneck', async () => {
    vi.mocked(api.get).mockResolvedValue(measured);
    vi.mocked(api.post).mockResolvedValue(check);
    const page = await render();
    await act(async () => {
      setValue(page.querySelector('input[name="ap_balance"]'), '600');
      setValue(page.querySelector('input[name="supply_chain_costs"]'), '900');
      setValue(page.querySelector('input[name="step-0-name"]'), 'Warehouse picking');
      setValue(page.querySelector('select[name="step-0-domain"]'), 'deliver');
      setValue(page.querySelector('input[name="step-0-throughput"]'), '200');
      setValue(page.querySelector('input[name="step-0-backlog"]'), '40');
      setValue(page.querySelector('input[name="step-0-utilization"]'), '95');
      setValue(page.querySelector('input[name="step-0-hours"]'), '2');
    });
    await submit(page);
    expect(api.post).toHaveBeenCalledWith('/api/insights/supply-chain-health/preview', {
      revenue: 10000, cogs: 3000, inventory_value: 800, ar_balance: 2000, ap_balance: 600, supply_chain_costs: 900,
      supplier_lead_times_days: [10, 14],
      process_steps: [{
        name: 'Warehouse picking', domain: 'deliver', throughput_per_day: 200, backlog: 40,
        utilization_pct: 95, avg_cycle_time_hours: 2,
      }],
    });
    const result = page.querySelector('[data-testid="check-result"]')?.textContent ?? '';
    expect(result).toContain('Calculated from the values you entered.');
    expect(result).toContain('Warehouse picking — Warehouse capacity');
    expect(result).toContain('Batch pick orders by zone');
    expect(result).toContain('Fair');
    expect(result).toContain('Resolve Warehouse picking bottleneck');
  });

  it('refuses to submit missing money figures', async () => {
    vi.mocked(api.get).mockResolvedValue(measured);
    const page = await render();
    await submit(page);
    expect(api.post).not.toHaveBeenCalled();
    expect(page.textContent).toContain('Fill in all six money figures');
  });
});
