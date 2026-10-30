import { afterEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../../lib/api';
import Anomalies, { meta, type AnomaliesResponse } from './anomalies';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><Anomalies /></MemoryRouter>);
  });
  await act(async () => {});
  return container;
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
  vi.restoreAllMocks();
});

const RESPONSE: AnomaliesResponse = {
  status: 'analysis_only',
  source: 'connected_data',
  requires_human_review: true,
  actions_executed: false,
  generated_at: '2026-10-28T12:00:00Z',
  as_of: '2026-10-28',
  lookback_days: 90,
  weeks_compared: 12,
  anomalies: [
    {
      kind: 'customer_order', severity: 'high', direction: 'high', value: 5000,
      usual_low: 0, usual_high: 3337.35, customer: 'Acme Corp', customer_id: 'QB1',
      order_id: 'acme-2', date: '2026-10-11',
      suggested_check: 'Confirm quantities and prices before shipping, and check stock and credit for an order this size.',
    },
    {
      kind: 'weekly_revenue', severity: 'medium', direction: 'low', value: 200,
      usual_low: 900, usual_high: 1400, period_start: '2026-10-07', period_end: '2026-10-13',
      suggested_check: 'Check for missing or delayed invoices and which regular customers did not order that week.',
    },
  ],
  counts: { high: 1, medium: 1 },
  orders_analyzed: 24,
  customers_checked: 2,
  customers_with_too_few_orders: 0,
  weekly_trends: 'checked',
  rows_skipped: 0,
};

describe('anomalies insight', () => {
  it('is offered only to roles the backend admits (view_financials)', () => {
    expect([...meta.roles].sort()).toEqual(['manager', 'owner']);
    expect(meta.group).toBe('Operations');
  });

  it('describes each unusual change in plain language with what to check', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue(RESPONSE);
    const page = await render();

    expect(get).toHaveBeenCalledWith('/api/insights/anomalies?lookback_days=90');
    const text = page.textContent ?? '';
    expect(text).toContain('Analysis only.');
    expect(text).toContain('Acme Corp placed an unusually large order');
    expect(text).toContain('was 5,000. Their usual orders are about 0–3,337');
    expect(text).toContain('Needs attention');
    expect(text).toContain('Sales were unusually low');
    expect(text).toContain('Worth a look');
    expect(text).toContain('Check for missing or delayed invoices');
    expect(page.querySelectorAll('ul[aria-label="Unusual changes found"] > li')).toHaveLength(2);
  });

  it('requests the chosen period', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue(RESPONSE);
    const page = await render();
    const select = page.querySelector('select') as HTMLSelectElement;
    await act(async () => {
      select.value = '180';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => {});
    expect(get).toHaveBeenLastCalledWith('/api/insights/anomalies?lookback_days=180');
  });

  it('explains when weekly totals were withheld', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({ ...RESPONSE, anomalies: [], weekly_trends: 'withheld', rows_skipped: 2 });
    const page = await render();
    expect(page.textContent).toContain('Weekly sales and order totals were not checked: 2 order(s) could not be read');
    expect(page.textContent).toContain('Nothing unusual found in the last 90 days.');
  });

  it('shows an error instead of "nothing unusual" when the source is down', async () => {
    vi.spyOn(api, 'get').mockRejectedValue(new Error('Order history is unavailable; no analysis was produced'));
    const page = await render();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Order history is unavailable');
    expect(page.textContent).not.toContain('Nothing unusual found');
  });
});
