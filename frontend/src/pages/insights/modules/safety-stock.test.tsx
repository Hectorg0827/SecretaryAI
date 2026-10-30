import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../../lib/api';
import SafetyStockInsight, { meta } from './safety-stock';

vi.mock('../../../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));

const advisory = {
  status: 'analysis_only', requires_human_review: true, actions_executed: false,
  generated_at: '2026-10-28T12:00:00Z',
};

const overview = {
  ...advisory,
  source: 'connected_data',
  items: [
    {
      item_id: 'QB-1', product_name: 'Malta', sku: 'MALT-1', status: 'calculated', reason: '',
      on_hand: 40, on_order: 100, stock_position: 140, safety_stock_units: 26, reorder_point_units: 146,
      suggested_order_qty: 494, stockout_risk_without_buffer: 0.6, avg_daily_sales: 10, lead_time_days_avg: 12,
      data_quality: 'sparse', notes: [], reorder_now: true, reorder_reason: 'Stock at or below reorder point.',
    },
    {
      item_id: 'QB-3', product_name: 'Coconut Water', sku: 'CW-1', status: 'insufficient_data',
      reason: 'No sales recorded in the last 90 days.', on_hand: 100, on_order: 0, reorder_now: false,
    },
  ],
  summary: { items_total: 2, items_calculated: 1, items_insufficient_data: 1, items_to_reorder: 1, rows_skipped: 3 },
};

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><SafetyStockInsight /></MemoryRouter>);
  });
  return container;
}

function setValue(el: Element | null, value: string) {
  const field = el as HTMLInputElement | HTMLSelectElement;
  const proto = field instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value')?.set?.call(field, value);
  field.dispatchEvent(new Event(field instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
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

describe('safety stock insight', () => {
  it('is offered to every role the backend view_inventory check admits', () => {
    expect([...meta.roles].sort()).toEqual(['back_office', 'manager', 'owner', 'sales_rep']);
    expect(meta.group).toBe('Supply chain');
  });

  it('renders connected recommendations and insufficient-data rows', async () => {
    vi.mocked(api.get).mockResolvedValue(overview);
    const page = await render();
    expect(api.get).toHaveBeenCalledWith('/api/insights/safety-stock?service_level=0.95');
    const text = page.textContent ?? '';
    expect(text).toContain('Malta');
    expect(text).toContain('146');
    expect(text).toContain('494');
    expect(text).toContain('Time to reorder');
    expect(text).toContain('No sales recorded in the last 90 days.');
    expect(text).toContain('3 unreadable records were skipped');
    expect(page.querySelector('[role="note"]')?.textContent).toContain('connected business data');
  });

  it('shows an error instead of an empty table when the source is down', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Inventory data is unavailable; no analysis was produced'));
    const page = await render();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Inventory data is unavailable');
    expect(page.textContent).not.toContain('Products checked');
  });

  it('submits the what-if form with parsed numbers', async () => {
    vi.mocked(api.get).mockResolvedValue(overview);
    vi.mocked(api.post).mockResolvedValue({
      ...advisory, source: 'user_supplied', product_name: 'Malta', safety_stock_units: 26,
      reorder_point_units: 146, suggested_order_qty: 494, stockout_risk_without_buffer: 0.6,
      avg_daily_sales: 10, lead_time_days_avg: 12, data_quality: 'sparse', notes: [],
      reorder_now: true, reorder_reason: 'Stock at or below reorder point.',
    });
    const page = await render();
    await act(async () => {
      setValue(page.querySelector('input[name="product_name"]'), 'Malta');
      setValue(page.querySelector('input[name="daily_sales"]'), '8, 12, 8, 12, 8, 12, 10');
      setValue(page.querySelector('input[name="lead_times_days"]'), '10 12 14');
      setValue(page.querySelector('input[name="unit_cost"]'), '6');
      setValue(page.querySelector('input[name="current_stock"]'), '140');
      setValue(page.querySelector('select[name="service_level"]'), '0.98');
    });
    await act(async () => {
      page.querySelector('section[aria-label="Try your own numbers"] form')
        ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(api.post).toHaveBeenCalledWith('/api/insights/safety-stock/preview', {
      product_name: 'Malta',
      daily_sales: [8, 12, 8, 12, 8, 12, 10],
      lead_times_days: [10, 12, 14],
      unit_cost: 6,
      current_stock: 140,
      service_level: 0.98,
    });
    const result = page.querySelector('[data-testid="preview-result"]');
    expect(result?.textContent).toContain('Calculated from the values you entered.');
    expect(result?.textContent).toContain('Reorder now.');
  });

  it('does not submit incomplete figures', async () => {
    vi.mocked(api.get).mockResolvedValue(overview);
    const page = await render();
    await act(async () => {
      setValue(page.querySelector('input[name="product_name"]'), 'Malta');
      setValue(page.querySelector('input[name="daily_sales"]'), '8, x');
    });
    await act(async () => {
      page.querySelector('section[aria-label="Try your own numbers"] form')
        ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(api.post).not.toHaveBeenCalled();
    expect(page.textContent).toContain('Fill in every field');
  });
});
