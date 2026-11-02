import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { PendingReceipts } from './PendingReceipts';
import { button, cleanup, confirm, click, dialog, mockApi, render, setRole } from './logisticsTestHarness';

const RECEIPT_ID = '0f8fad5b-d9cb-469f-a165-70867728950e';

function pending(canApprove = true) {
  return {
    pending_receipts: [{
      id: RECEIPT_ID, po_id: 'po1', receipt_key: 'Slip 55', submitted_by: 'clerk-1',
      created_at: '2026-10-20T09:00:00Z', can_approve: canApprove,
      payload: [{ sku: 'CASE-1', lot_code: 'LOT-9', accepted_qty: 90, damaged_qty: 6, quarantined_qty: 4 }],
    }],
    count: 1,
  };
}

const ORDERS = { orders: [{ id: 'po1', po_number: 'PO 1200' }] };

afterEach(cleanup);

describe('deliveries waiting for approval', () => {
  it('lists pending deliveries with their purchase order and lines', async () => {
    setRole('manager', 'approver-1');
    const { get } = mockApi({ '/api/logistics/receipts/pending': pending(), '/api/logistics/orders': ORDERS });
    const container = await render(<PendingReceipts />);
    expect(get).toHaveBeenCalledWith('/api/logistics/receipts/pending');
    expect(container.textContent).toContain('Purchase order PO 1200 · receipt Slip 55');
    expect(container.textContent).toContain('LOT-9');
  });

  it('requires confirmation that says stock changes, then reports the server result', async () => {
    setRole('owner', 'approver-1');
    const path = `/api/logistics/receipts/${RECEIPT_ID}/approve`;
    const { post, getRoutes } = mockApi(
      { '/api/logistics/receipts/pending': pending(), '/api/logistics/orders': ORDERS },
      { [path]: {
        receipt_id: 'r1', staged_receipt_id: RECEIPT_ID, po_status: 'partially_received', status: 'posted',
        replayed: false, inventory_posted: true, accounting_reconciled: false,
      } },
    );
    const container = await render(<PendingReceipts />);
    await click(button(container, 'Approve and add to stock'));
    expect(post).not.toHaveBeenCalled();
    const text = dialog(container)?.textContent ?? '';
    expect(text).toContain('90 cases to available stock, 6 to damaged stock and 4 to the inspection hold');
    expect(text).toContain('cannot be undone');
    expect(text).toContain('Nothing is written to QuickBooks');

    getRoutes['/api/logistics/receipts/pending'] = { pending_receipts: [], count: 0 };
    await confirm(container);
    expect(post).toHaveBeenCalledWith(path, {});
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      'Approved by the server: receipt Slip 55 was added to stock. Purchase order status: partially received.');
    expect(container.textContent).toContain('No deliveries are waiting for approval.');
  });

  it('does not report success when the server did not confirm stock posting', async () => {
    setRole('owner', 'approver-1');
    const path = `/api/logistics/receipts/${RECEIPT_ID}/approve`;
    mockApi(
      { '/api/logistics/receipts/pending': pending(), '/api/logistics/orders': ORDERS },
      { [path]: { receipt_id: 'r1', staged_receipt_id: RECEIPT_ID, status: 'posted', replayed: false, inventory_posted: false, accounting_reconciled: false } },
    );
    const container = await render(<PendingReceipts />);
    await click(button(container, 'Approve and add to stock'));
    await confirm(container);
    expect(container.textContent).toContain('The server did not confirm that receipt Slip 55 was added to stock');
    expect(container.querySelector('[role="status"]')).toBeNull();
  });

  it('shows the server refusal for an approval conflict', async () => {
    setRole('owner', 'approver-1');
    const path = `/api/logistics/receipts/${RECEIPT_ID}/approve`;
    mockApi(
      { '/api/logistics/receipts/pending': pending(), '/api/logistics/orders': ORDERS },
      { [path]: new Error('Operation conflicts with approval status, quantity or idempotency rules') },
    );
    const container = await render(<PendingReceipts />);
    await click(button(container, 'Approve and add to stock'));
    await confirm(container);
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Operation conflicts with approval status');
  });

  it('hides approval for the person who submitted the delivery', async () => {
    setRole('manager', 'clerk-1');
    mockApi({ '/api/logistics/receipts/pending': pending(false), '/api/logistics/orders': ORDERS });
    const container = await render(<PendingReceipts />);
    expect(button(container, 'Approve and add to stock')).toBeUndefined();
    expect(container.textContent).toContain('You submitted this delivery');
  });

  it('does not load or offer approvals to back office', async () => {
    setRole('back_office');
    const { get } = mockApi();
    const container = await render(<PendingReceipts />);
    expect(get).not.toHaveBeenCalled();
    expect(button(container, 'Approve and add to stock')).toBeUndefined();
    expect(container.textContent).toContain('approved by an owner or manager');
  });

  it('shows an unavailable list as an error, not as an empty queue', async () => {
    setRole('owner');
    mockApi({ '/api/logistics/receipts/pending': new Error('Purchasing records unavailable'), '/api/logistics/orders': ORDERS });
    const container = await render(<PendingReceipts />);
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Purchasing records unavailable');
    expect(container.textContent).not.toContain('No deliveries are waiting');
  });
});
