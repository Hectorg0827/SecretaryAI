import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { PurchaseCoveragePanel } from './PurchaseCoveragePanel';
import { cleanup, mockApi, render, setRole, click, button } from './logisticsTestHarness';

afterEach(cleanup);

const ENDPOINT = '/api/logistics/journey/purchasing-coverage';
const example = {
  purchase_order_count: 2, not_linked_count: 1, unverified_report_count: 1,
  review_required_count: 2,
  source: 'tenant_scoped_purchasing_orders_and_logistics_events',
  shipment_status_verified_by_po_link: false,
  inventory_posted_by_this_operation: false,
  orders: [
    {
      po_number: 'PO-1001', po_status: 'approved', supplier_ref: 'SUP-1',
      shipment_link_status: 'not_linked', linked_shipment_ids: [],
      reported_unverified_shipment_ids: [], requires_review: true,
      shipping_status_verified: false, container_receipt_verified: false,
      warehouse_receipt_attributed_to_shipment: false,
    },
    {
      po_number: 'PO-1002', po_status: 'received', supplier_ref: 'SUP-2',
      shipment_link_status: 'unverified_report_only', linked_shipment_ids: [],
      reported_unverified_shipment_ids: ['SHP-UNVERIFIED'], requires_review: true,
      shipping_status_verified: false, container_receipt_verified: false,
      warehouse_receipt_attributed_to_shipment: false,
    },
  ],
};

describe('recorded purchase-to-shipment coverage', () => {
  it('surfaces unlinked POs and unverified shipment claims without inventing ETA', async () => {
    setRole('manager');
    const { get, post } = mockApi({ [ENDPOINT]: example });
    const container = await render(<PurchaseCoveragePanel />);
    expect(get).toHaveBeenCalledWith(ENDPOINT);
    expect(container.textContent).toContain('No shipment journey recorded');
    expect(container.textContent).toContain('Reported in unverified source only');
    expect(container.textContent).toContain('SHP-UNVERIFIED');
    expect(container.textContent).toContain('does not confirm a container');
    expect(post).not.toHaveBeenCalled();
    await click(button(container, 'Refresh PO coverage'));
    expect(get).toHaveBeenCalledTimes(2);
  });

  it('shows an outage as an error not an empty PO list', async () => {
    setRole('back_office');
    mockApi({ [ENDPOINT]: new Error('Purchasing temporarily unavailable') });
    const container = await render(<PurchaseCoveragePanel />);
    expect(container.querySelector('[role="alert"]')?.textContent)
      .toContain('Purchasing temporarily unavailable');
    expect(container.textContent).not.toContain('No saved POs');
  });

  it('rejects a payload that falsely asserts verified container receipt', async () => {
    setRole('owner');
    mockApi({ [ENDPOINT]: {
      ...example, orders: [{
        ...example.orders[0], container_receipt_verified: true,
      }, example.orders[1]],
    } });
    const container = await render(<PurchaseCoveragePanel />);
    expect(container.querySelector('[role="alert"]')?.textContent)
      .toContain('response is incomplete');
  });

  it('does not fetch cross-company procurement records for sales roles', async () => {
    setRole('sales_rep');
    const { get } = mockApi({});
    const container = await render(<PurchaseCoveragePanel />);
    expect(get).not.toHaveBeenCalled();
    expect(container.textContent).toBe('');
  });

  it('distinguishes an actually empty saved PO source from missing source data', async () => {
    setRole('manager');
    mockApi({ [ENDPOINT]: {
      purchase_order_count: 0, not_linked_count: 0, unverified_report_count: 0,
      review_required_count: 0, orders: [],
      source: 'tenant_scoped_purchasing_orders_and_logistics_events',
      shipment_status_verified_by_po_link: false,
      inventory_posted_by_this_operation: false,
    } });
    const container = await render(<PurchaseCoveragePanel />);
    expect(container.textContent).toContain('does not establish whether outside purchasing records exist');
  });
});
