import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { PurchasingRecords } from './PurchasingRecords';
import {
  button, cleanup, click, confirm, dialog, field, mockApi, render, setRole, type,
} from './logisticsTestHarness';

const WAREHOUSE_ID = '5b0c6f0e-8f5c-4c47-9f39-6a1e1f7a4c11';

function orderRow(status: string, createdBy = 'someone-else') {
  return { id: 'po1', po_number: 'PO 1200', supplier_ref: 'Pilot supplier', status, created_at: '2026-10-01T10:00:00Z', created_by: createdBy };
}

function detailFor(status: string, createdBy = 'someone-else') {
  return {
    order: orderRow(status, createdBy),
    lines: [{ sku: 'CASE-1', qty_ordered: 1200, qty_received: 200 }],
    staged_receipts: [{ id: 's1', receipt_key: 'Receipt A', status: 'posted' }],
    posted_receipts: [{ id: 'r1', receipt_key: 'Receipt A' }],
    ordered_cases: '1200', received_cases: '200', remaining_cases: '1000', accounting_reconciled: false,
  };
}

function routesFor(status: string, createdBy = 'someone-else') {
  return {
    '/api/logistics/orders': { orders: [orderRow(status, createdBy)] },
    '/api/logistics/orders/PO%201200': detailFor(status, createdBy),
    '/api/logistics/pipeline/PO%201200': new Error('Procurement cycle not found'),
    '/api/logistics/orders/PO%201200/evidence': {
      evidence_claims: [], claim_count: 0, unverified_claim_totals_by_currency: {}, evidence_status_message: '',
    },
  };
}

async function selectOrder(container: HTMLElement) {
  await type(container.querySelector('#saved-po'), 'PO 1200');
}

afterEach(cleanup);

describe('saved purchasing records', () => {
  it('shows a failed API read as an error, never an empty successful ledger', async () => {
    setRole('owner');
    mockApi({ '/api/logistics/orders': new Error('Subscription required') });
    const container = await render(<PurchasingRecords />);
    expect(container.querySelector('[role="alert"]')?.textContent).toBe('Subscription required');
    expect(container.textContent).not.toContain('No saved purchase orders.');
  });

  it('reports an actual empty ledger and allows refresh', async () => {
    setRole('owner');
    const { get } = mockApi({ '/api/logistics/orders': { orders: [] } });
    const container = await render(<PurchasingRecords />);
    expect(get).toHaveBeenCalledWith('/api/logistics/orders');
    expect(container.textContent).toContain('No saved purchase orders.');
    await click(button(container, 'Refresh'));
    expect(get).toHaveBeenCalledTimes(2);
  });

  it('loads selected receipt records without claiming accounting reconciliation', async () => {
    setRole('owner');
    const { get } = mockApi(routesFor('approved'));
    const container = await render(<PurchasingRecords />);
    await selectOrder(container);
    expect(get).toHaveBeenCalledWith('/api/logistics/orders/PO%201200');
    expect(get).toHaveBeenCalledWith('/api/logistics/pipeline/PO%201200');
    expect(container.textContent).toContain('CASE-1');
    expect(container.textContent).toContain('Receipt A');
    expect(container.textContent).toContain('QuickBooks reconciliation is not verified');
    expect(container.textContent).toContain('must not be treated as sellable stock');
    expect(container.textContent).toContain('No shipment tracking has been recorded for this order.');
  });
});

describe('creating a draft purchase order', () => {
  it('requires confirmation, sends the exact body and shows the server status', async () => {
    setRole('back_office');
    const { post } = mockApi(
      {
        '/api/logistics/orders': { orders: [] },
        '/api/logistics/warehouses': { warehouses: [{ id: WAREHOUSE_ID, code: 'MAIN', name: 'Main warehouse' }] },
      },
      { '/api/logistics/orders': { po_id: 'new-id', po_number: 'PO-77', status: 'draft', line_count: 2, inventory_posted: false } },
    );
    const container = await render(<PurchasingRecords />);
    await click(button(container, 'New purchase order'));
    await type(container.querySelector('#new-po-number'), ' PO-77 ');
    await type(container.querySelector('#new-po-supplier'), 'Acme Imports');
    await type(container.querySelector('#new-po-warehouse'), WAREHOUSE_ID);
    await type(container.querySelector('#new-po-sku-0'), 'CASE-1');
    await type(container.querySelector('#new-po-cases-0'), '40');
    await click(button(container, 'Add product'));
    await type(container.querySelector('#new-po-sku-1'), 'CASE-2');
    await type(container.querySelector('#new-po-cases-1'), '10');
    await click(button(container, 'Review draft'));

    expect(post).not.toHaveBeenCalled();
    const panel = dialog(container);
    expect(panel?.textContent).toContain('PO-77');
    expect(panel?.textContent).toContain('50 cases in total');
    expect(panel?.textContent).toContain('not sent to the supplier');
    expect(panel?.textContent).toContain('QuickBooks');

    await confirm(container);
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith('/api/logistics/orders', {
      po_number: 'PO-77', supplier_ref: 'Acme Imports', warehouse_id: WAREHOUSE_ID,
      lines: [{ sku: 'CASE-1', qty_ordered: 40 }, { sku: 'CASE-2', qty_ordered: 10 }],
    });
    expect(container.querySelector('[role="status"]')?.textContent).toContain('Saved by the server: purchase order PO-77 is now "Draft — waiting for approval"');
  });

  it('can be cancelled without sending anything and rejects duplicate SKUs locally', async () => {
    setRole('owner');
    const { post } = mockApi({
      '/api/logistics/orders': { orders: [] },
      '/api/logistics/warehouses': { warehouses: [{ id: WAREHOUSE_ID, code: 'MAIN', name: 'Main' }] },
    });
    const container = await render(<PurchasingRecords />);
    await click(button(container, 'New purchase order'));
    await type(container.querySelector('#new-po-number'), 'PO-1');
    await type(container.querySelector('#new-po-supplier'), 'Acme');
    await type(container.querySelector('#new-po-warehouse'), WAREHOUSE_ID);
    await type(container.querySelector('#new-po-sku-0'), 'CASE-1');
    await type(container.querySelector('#new-po-cases-0'), '5');
    await click(button(container, 'Add product'));
    await type(container.querySelector('#new-po-sku-1'), 'CASE-1');
    await type(container.querySelector('#new-po-cases-1'), '5');
    await click(button(container, 'Review draft'));
    expect(dialog(container)).toBeNull();
    expect(container.textContent).toContain('listed more than once');

    await click(button(container, 'Remove'));
    await click(button(container, 'Review draft'));
    expect(dialog(container)).not.toBeNull();
    await click(button(dialog(container)!, 'Cancel'));
    expect(dialog(container)).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it('fails closed when the warehouse list cannot be loaded', async () => {
    setRole('owner');
    mockApi({
      '/api/logistics/orders': { orders: [] },
      '/api/logistics/warehouses': new Error('Purchasing records unavailable'),
    });
    const container = await render(<PurchasingRecords />);
    await click(button(container, 'New purchase order'));
    expect(container.textContent).toContain('Purchasing records unavailable');
    expect(button(container, 'Review draft')).toBeUndefined();
  });

  it('is not offered to roles the server rejects', async () => {
    setRole('sales_rep');
    mockApi({ '/api/logistics/orders': { orders: [] } });
    const container = await render(<PurchasingRecords />);
    expect(button(container, 'New purchase order')).toBeUndefined();
  });
});

describe('approving a draft purchase order', () => {
  it('confirms first, posts an empty body and reports the server status', async () => {
    setRole('manager', 'approver-1');
    const { post, getRoutes } = mockApi(routesFor('draft'), {
      '/api/logistics/orders/PO%201200/approve': { po_id: 'po1', status: 'approved', inventory_posted: false },
    });
    const container = await render(<PurchasingRecords />);
    await selectOrder(container);
    await click(button(container, 'Approve purchase order'));
    expect(post).not.toHaveBeenCalled();
    expect(dialog(container)?.textContent).toContain('Nothing is sent to the supplier');

    getRoutes['/api/logistics/orders'] = { orders: [orderRow('approved')] };
    getRoutes['/api/logistics/orders/PO%201200'] = detailFor('approved');
    await confirm(container);
    expect(post).toHaveBeenCalledWith('/api/logistics/orders/PO%201200/approve', {});
    expect(container.textContent).toContain('Approved by the server: purchase order PO 1200 is now "Approved — ready to receive"');
  });

  it('explains a server conflict instead of claiming success', async () => {
    setRole('owner', 'approver-1');
    mockApi(routesFor('draft'), {
      '/api/logistics/orders/PO%201200/approve': new Error('Operation conflicts with approval status, quantity or idempotency rules'),
    });
    const container = await render(<PurchasingRecords />);
    await selectOrder(container);
    await click(button(container, 'Approve purchase order'));
    await confirm(container);
    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Operation conflicts with approval status');
    expect(alert?.textContent).toContain('Refresh to see the latest status');
    expect(container.textContent).not.toContain('Approved by the server');
  });

  it('explains a permission refusal and an unavailable server', async () => {
    setRole('owner', 'approver-1');
    const { postRoutes } = mockApi(routesFor('draft'), {
      '/api/logistics/orders/PO%201200/approve': new Error('Purchase approval not permitted'),
    });
    const container = await render(<PurchasingRecords />);
    await selectOrder(container);
    await click(button(container, 'Approve purchase order'));
    await confirm(container);
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('You are not allowed to do this step');

    postRoutes['/api/logistics/orders/PO%201200/approve'] = new Error('Purchasing database unavailable');
    await click(button(container, 'Approve purchase order'));
    await confirm(container);
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Refresh to check whether anything changed');
  });

  it('is hidden from back office and from the person who created the draft', async () => {
    setRole('back_office', 'clerk-1');
    mockApi(routesFor('draft'));
    let container = await render(<PurchasingRecords />);
    await selectOrder(container);
    expect(button(container, 'Approve purchase order')).toBeUndefined();
    expect(container.textContent).toContain('must be approved by an owner or manager');
    await cleanup();

    setRole('manager', 'creator-1');
    mockApi(routesFor('draft', 'creator-1'));
    container = await render(<PurchasingRecords />);
    await selectOrder(container);
    expect(button(container, 'Approve purchase order')).toBeUndefined();
    expect(container.textContent).toContain('You created this draft');
  });
});

describe('recording a delivery against an approved order', () => {
  it('confirms that stock will not change yet and sends the exact receipt lines', async () => {
    setRole('back_office', 'clerk-1');
    const { post } = mockApi(routesFor('approved'), {
      '/api/logistics/orders/PO%201200/receipts': {
        staged_receipt_id: 'stage-1', status: 'pending', replayed: false, inventory_posted: false,
      },
    });
    const container = await render(<PurchasingRecords />);
    await selectOrder(container);
    await type(field(container, 'Receipt reference'), 'Slip 55');
    await type(container.querySelector('[aria-label="CASE-1 lot code"]'), 'LOT-9');
    await type(container.querySelector('[aria-label="CASE-1 good cases"]'), '90');
    await type(container.querySelector('[aria-label="CASE-1 damaged cases"]'), '6');
    await type(container.querySelector('[aria-label="CASE-1 cases held for inspection"]'), '4');
    await click(button(container, 'Review delivery'));
    expect(post).not.toHaveBeenCalled();
    expect(dialog(container)?.textContent).toContain('Your stock does not change yet');
    expect(dialog(container)?.textContent).toContain('90 good, 6 damaged and 4 held for inspection');

    await confirm(container);
    expect(post).toHaveBeenCalledWith('/api/logistics/orders/PO%201200/receipts', {
      receipt_key: 'Slip 55',
      lines: [{ sku: 'CASE-1', lot_code: 'LOT-9', accepted_qty: 90, damaged_qty: 6, quarantined_qty: 4 }],
    });
    expect(container.textContent).toContain('Submitted. Server status: waiting for approval. Stock has not changed yet.');
  });

  it('blocks more cases than are still expected before sending', async () => {
    setRole('owner');
    const { post } = mockApi(routesFor('approved'));
    const container = await render(<PurchasingRecords />);
    await selectOrder(container);
    await type(field(container, 'Receipt reference'), 'Slip 56');
    await type(container.querySelector('[aria-label="CASE-1 good cases"]'), '1001');
    await click(button(container, 'Review delivery'));
    expect(dialog(container)).toBeNull();
    expect(container.textContent).toContain('more than the 1000 still expected');
    expect(post).not.toHaveBeenCalled();
  });

  it('is not offered for a draft order', async () => {
    setRole('owner', 'approver-1');
    mockApi(routesFor('draft'));
    const container = await render(<PurchasingRecords />);
    await selectOrder(container);
    expect(button(container, 'Review delivery')).toBeUndefined();
  });
});
