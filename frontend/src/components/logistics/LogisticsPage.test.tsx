import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { Logistics } from '../../pages/Logistics';
import { button, cleanup, click, mockApi, render, setRole } from './logisticsTestHarness';

afterEach(cleanup);

describe('logistics page tabs', () => {
  it('opens the receiving, warehouse, returns and tools screens', async () => {
    setRole('manager');
    const { get } = mockApi({
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
