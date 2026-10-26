import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../lib/api';
import { PurchasingRecords } from './PurchasingRecords';

let root: Root;
let container: HTMLDivElement;
async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(<PurchasingRecords />); });
}
afterEach(async () => {
  if (root) await act(async () => root.unmount());
  container?.remove();
  vi.restoreAllMocks();
});

describe('saved purchasing records', () => {
  it('shows a failed API read as an error, never an empty successful ledger', async () => {
    vi.spyOn(api, 'get').mockRejectedValue(new Error('Subscription required'));
    await render();
    expect(container.querySelector('[role="alert"]')?.textContent).toBe('Subscription required');
    expect(container.textContent).not.toContain('No saved purchase orders.');
  });

  it('reports an actual empty ledger and allows refresh', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue({ orders: [] });
    await render();
    expect(get).toHaveBeenCalledWith('/api/logistics/orders');
    expect(container.textContent).toContain('No saved purchase orders.');
    await act(async () => { container.querySelector('button')!.click(); });
    expect(get).toHaveBeenCalledTimes(2);
  });

  it('loads selected receipt records without claiming accounting reconciliation', async () => {
    const order = { id: 'po1', po_number: 'PO 1200', supplier_ref: 'Pilot supplier', status: 'approved' };
    const get = vi.spyOn(api, 'get').mockResolvedValueOnce({ orders: [order] }).mockResolvedValueOnce({
      order, lines: [{ sku: 'CASE-1', qty_ordered: 1200, qty_received: 1200 }],
      staged_receipts: [{ id: 's1', receipt_key: 'Receipt A', status: 'approved' }],
      posted_receipts: [{ id: 'r1', receipt_key: 'Receipt A' }],
      ordered_cases: '1200', received_cases: '1200', remaining_cases: '0', accounting_reconciled: false,
    });
    await render();
    await act(async () => {
      const select = container.querySelector('select')!;
      select.value = 'PO 1200';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(get).toHaveBeenLastCalledWith('/api/logistics/orders/PO%201200');
    expect(container.textContent).toContain('CASE-1');
    expect(container.textContent).toContain('Receipt A');
    expect(container.textContent).toContain('QuickBooks reconciliation is not verified');
    expect(container.textContent).toContain('must not be treated as sellable stock');
  });
});
