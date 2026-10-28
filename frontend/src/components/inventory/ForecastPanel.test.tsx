import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../lib/api';
import { Inventory } from '../../pages/Inventory';
import { ForecastPanel, recentWeeklyAverage, type ItemForecast } from './ForecastPanel';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function mount(node: ReactNode) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root?.render(node); });
  return container;
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
  localStorage.clear();
  vi.restoreAllMocks();
});

function isoDaysFromToday(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function shown(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function forecast(overrides: Partial<ItemForecast> = {}): ItemForecast {
  return {
    product_name: 'Synthetic Malta 12oz',
    current_stock: 120,
    projected_stockout_date: isoDaysFromToday(84),
    recommended_reorder_date: isoDaysFromToday(42),
    recommended_order_qty: 80,
    weekly_sell_history: [10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10],
    historical_invoice_count: 12,
    lead_time_weeks: 6,
    lead_time_source: 'industry_default',
    data_source: 'quickbooks_invoices',
    confidence: 'high',
    ...overrides,
  };
}

describe('demand forecast panel', () => {
  it('requests the item forecast and renders the numbers and reorder date', async () => {
    const data = forecast();
    const get = vi.spyOn(api, 'get').mockResolvedValue(data);
    const page = await mount(<ForecastPanel itemId="QB 1/2" productName="Synthetic Malta 12oz" onClose={() => {}} />);

    expect(get).toHaveBeenCalledWith('/api/inventory/QB%201%2F2/forecast');
    const text = page.textContent ?? '';
    expect(text).toContain('Recommended reorder date');
    expect(text).toContain(shown(data.recommended_reorder_date!));
    expect(text).toContain(shown(data.projected_stockout_date!));
    expect(text).toContain('120 units');
    expect(text).toContain('80 units');
    expect(text).toContain('High confidence');
    expect(text).toContain('Based on 12 QuickBooks invoices with sales in 12 of the last 12 weeks.');
    expect(text).toContain('6 weeks (typical default');
    expect(text).not.toContain('Not enough sales history');
  });

  it('says to reorder now when the recommended date has passed', async () => {
    const data = forecast({ recommended_reorder_date: isoDaysFromToday(-3) });
    vi.spyOn(api, 'get').mockResolvedValue(data);
    const page = await mount(<ForecastPanel itemId="QB-1" productName="Malta" onClose={() => {}} />);
    expect(page.textContent).toContain('Reorder now');
    expect(page.textContent).toContain(`The recommended date (${shown(data.recommended_reorder_date!)}) has already passed.`);
  });

  it('shows insufficient history instead of inventing a forecast', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(forecast({
      weekly_sell_history: [], historical_invoice_count: 0, confidence: 'low',
      projected_stockout_date: null, recommended_reorder_date: null, recommended_order_qty: 0,
    }));
    const page = await mount(<ForecastPanel itemId="QB-1" productName="Malta" onClose={() => {}} />);
    const text = page.textContent ?? '';
    expect(text).toContain('Not enough sales history to forecast');
    expect(text).toContain('no run-out date, reorder date or order quantity is suggested');
    expect(text).not.toContain('Recommended reorder date');
    expect(text).not.toContain('Reorder now');
  });

  it('explains when recent sales are zero so no run-out date exists', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(forecast({
      weekly_sell_history: [5, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], historical_invoice_count: 2,
      confidence: 'low', projected_stockout_date: null, recommended_reorder_date: null, recommended_order_qty: 0,
    }));
    const page = await mount(<ForecastPanel itemId="QB-1" productName="Malta" onClose={() => {}} />);
    const text = page.textContent ?? '';
    expect(text).toContain('No reorder date suggested');
    expect(text).toContain('Not projected');
    expect(text).toContain('Limited sales history');
  });

  it('shows an unavailable sales source (503) as an error with retry, not a forecast', async () => {
    const get = vi.spyOn(api, 'get')
      .mockRejectedValueOnce(new Error('Historical sales data unavailable'))
      .mockResolvedValueOnce(forecast());
    const page = await mount(<ForecastPanel itemId="QB-1" productName="Malta" onClose={() => {}} />);
    const alert = page.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('No forecast was made: Historical sales data unavailable');
    expect(page.textContent).not.toContain('Recommended reorder date');

    const retry = [...page.querySelectorAll('button')].find((b) => b.textContent === 'Try again')!;
    await act(async () => { retry.click(); });
    expect(get).toHaveBeenCalledTimes(2);
    expect(page.textContent).toContain('Recommended reorder date');
  });

  it('shows an unknown item (404) as an error', async () => {
    vi.spyOn(api, 'get').mockRejectedValue(new Error('Inventory item not found'));
    const page = await mount(<ForecastPanel itemId="gone" productName="Gone" onClose={() => {}} />);
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Inventory item not found');
  });

  it('averages the last four weeks like the backend forecast', () => {
    expect(recentWeeklyAverage([100, 100, 2, 4, 6, 8])).toBe(5);
    expect(recentWeeklyAverage([])).toBe(0);
  });
});

describe('inventory page forecast entry point', () => {
  const items = [
    { item_id: 'QB-1', product_name: 'Synthetic Malta 12oz', total_qty: 120, weeks_remaining: 12, stock_status: 'healthy', needs_po: false },
    { item_id: 'QB-2', product_name: 'Synthetic Kola 2L', total_qty: 4, weeks_remaining: 0.5, stock_status: 'critical', needs_po: true },
  ];

  it('lists healthy and alert items and opens a product forecast', async () => {
    const get = vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
      if (path === '/api/inventory') return { items };
      return forecast();
    });
    const page = await mount(<Inventory />);
    expect(page.textContent).toContain('Synthetic Malta 12oz');
    expect(page.textContent).toContain('In Stock');
    expect(page.textContent).not.toContain('PO exists');

    const button = page.querySelector('button[aria-label="View demand forecast for Synthetic Malta 12oz"]') as HTMLButtonElement;
    await act(async () => { button.click(); });
    expect(get).toHaveBeenCalledWith('/api/inventory/QB-1/forecast');
    expect(page.querySelector('[role="dialog"]')?.textContent).toContain('Recommended reorder date');

    const close = page.querySelector('button[aria-label="Close forecast"]') as HTMLButtonElement;
    await act(async () => { close.click(); });
    expect(page.querySelector('[role="dialog"]')).toBeNull();
  });

  it('shows a failed inventory read as an error, never as all-healthy stock', async () => {
    vi.spyOn(api, 'get').mockRejectedValue(new Error('QuickBooks not connected'));
    const page = await mount(<Inventory />);
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('QuickBooks not connected');
    expect(page.textContent).not.toContain('healthy stock levels');
  });
});
