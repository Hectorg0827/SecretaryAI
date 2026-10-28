import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { Logistics } from '../../pages/Logistics';
import { button, cleanup, click, mockApi, render, setRole } from './logisticsTestHarness';

afterEach(cleanup);

describe('logistics page tabs', () => {
  it('opens the receiving, warehouse, returns and tools screens', async () => {
    setRole('manager');
    const { get } = mockApi({
      '/api/inventory/alerts': { alerts: [], count: 0 },
      '/api/logistics/orders': { orders: [] },
      '/api/logistics/receipts/pending': { pending_receipts: [], count: 0 },
    });
    const container = await render(<Logistics />);

    await click(button(container, 'Receiving'));
    expect(get).toHaveBeenCalledWith('/api/logistics/receipts/pending');
    expect(container.textContent).toContain('No deliveries are waiting for approval.');
    expect(container.textContent).toContain('Stock check against QuickBooks');

    await click(button(container, 'Warehouse'));
    expect(container.textContent).toContain('Pick plan');

    await click(button(container, 'Returns'));
    expect(container.textContent).toContain('Review a customer return');

    await click(button(container, 'Customs & Seasonality'));
    expect(container.textContent).toContain('Read a customs broker email');
    expect(container.textContent).toContain('Seasonal demand pattern');
  });
});

describe('logistics page for back office', () => {
  it('opens purchasing screens without calling owner/manager-only endpoints', async () => {
    setRole('back_office');
    const { get } = mockApi({
      '/api/inventory/alerts': { alerts: [], count: 0 },
      '/api/logistics/orders': { orders: [] },
    });
    const container = await render(<Logistics />);

    await click(button(container, 'Purchase Orders'));
    expect(get).toHaveBeenCalledWith('/api/logistics/orders');

    await click(button(container, 'Receiving'));
    // GET /receipts/pending needs approve_actions; back office would get 403.
    expect(get).not.toHaveBeenCalledWith('/api/logistics/receipts/pending');
    expect(container.textContent).toContain('Stock check against QuickBooks');
  });
});

const ALERTS = [
  { item_id: 'QB-LOW', product_name: 'Synthetic Low Item', total_qty: 30, weeks_remaining: 3.5, stock_status: 'low', needs_po: false },
  { item_id: 'QB-OUT', product_name: 'Synthetic Empty Item', total_qty: 0, weeks_remaining: null, stock_status: 'out_of_stock', needs_po: true },
  { item_id: 'QB-CRIT', product_name: 'Synthetic Critical Item', total_qty: 4, weeks_remaining: 0.8, stock_status: 'critical', needs_po: true },
];

describe('logistics reorder queue', () => {
  it('lists products to reorder from the inventory alerts, most urgent first', async () => {
    setRole('back_office');
    const { get } = mockApi({ '/api/inventory/alerts': { alerts: ALERTS, count: 3 } });
    const container = await render(<Logistics />);

    expect(get).toHaveBeenCalledWith('/api/inventory/alerts');
    const rows = Array.from(container.querySelectorAll('tbody tr'));
    expect(rows.map((row) => row.querySelector('td')?.textContent)).toEqual([
      'Synthetic Empty ItemQB-OUT',
      'Synthetic Critical ItemQB-CRIT',
      'Synthetic Low ItemQB-LOW',
    ]);
    expect(rows[0].textContent).toContain('Out of stock');
    expect(rows[0].textContent).toContain('Unknown');
    expect(rows[1].textContent).toContain('~0.8 wks');
    expect(rows[2].textContent).toContain('Not yet');
    expect(container.textContent).toContain('Products to reorder');
    expect(container.textContent).toContain('Products without a recorded sales rate only appear here once they run out.');
    // The tab badge and header count come from the loaded list.
    expect(button(container, 'Reorder Queue3')).toBeDefined();
  });

  it('shows a failed read as an error, never as an empty queue', async () => {
    setRole('manager');
    mockApi({ '/api/inventory/alerts': new Error('Inventory source unavailable') });
    const container = await render(<Logistics />);

    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Inventory source unavailable');
    expect(container.textContent).not.toContain('No products are low or out of stock');
    expect(container.querySelector('table')).toBeNull();
  });

  it('treats a malformed response as unavailable', async () => {
    setRole('manager');
    mockApi({ '/api/inventory/alerts': { count: 0 } });
    const container = await render(<Logistics />);
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('The reorder list is unavailable.');
  });

  it('says plainly when nothing needs reordering, with what that is based on', async () => {
    setRole('owner');
    mockApi({ '/api/inventory/alerts': { alerts: [], count: 0 } });
    const container = await render(<Logistics />);
    expect(container.textContent).toContain('No products are low or out of stock right now.');
    expect(container.textContent).toContain('Based on QuickBooks stock levels and recorded sales rates.');
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it('reloads the list on refresh', async () => {
    setRole('manager');
    let calls = 0;
    const { get } = mockApi({
      '/api/inventory/alerts': () => {
        calls += 1;
        if (calls === 1) throw new Error('Inventory source unavailable');
        return { alerts: ALERTS, count: 3 };
      },
    });
    const container = await render(<Logistics />);
    expect(container.querySelector('[role="alert"]')).not.toBeNull();

    await click(button(container, 'Refresh'));
    expect(get).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(container.querySelectorAll('tbody tr')).toHaveLength(3);
  });
});

describe('logistics tabs without a data source', () => {
  it('does not offer Shipments or Cost Analysis tabs', async () => {
    setRole('owner');
    mockApi({ '/api/inventory/alerts': { alerts: [], count: 0 } });
    const container = await render(<Logistics />);
    const labels = Array.from(container.querySelectorAll('button')).map((b) => b.textContent?.trim());
    expect(labels).not.toContain('Shipments');
    expect(labels).not.toContain('Cost Analysis');
    expect(labels).toEqual(expect.arrayContaining([
      'Reorder Queue', 'Purchase Orders', 'Receiving', 'Warehouse', 'Returns', 'Customs & Seasonality',
    ]));
  });
});
