import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { PurchaseCrosscheck } from './PurchaseCrosscheck';
import { cleanup, mockApi, render, setRole } from './logisticsTestHarness';

afterEach(cleanup);

const ENDPOINT = '/api/logistics/journey/shipments/SHP%2F1/purchasing-crosscheck';

describe('shipment purchasing crosscheck', () => {
  it('renders posted receipts as PO-level only, never proven shipment receipt', async () => {
    setRole('manager');
    const { get, post } = mockApi({
      [ENDPOINT]: {
        shipment_id: 'SHP/1',
        status: 'shared_po_needs_allocation_review',
        shared_po_numbers: ['PO-1'],
        po_results: [{
          po_number: 'PO-1', status: 'received',
          ordered_cases: '1200', received_cases_all_conditions: '1200',
          remaining_cases: '0', posted_receipt_count: 1,
        }],
        posted_receipts_prove_shipment_received: false,
        shipment_inventory_reconciled: false, accounting_reconciled: false,
        review_required: true,
      },
    });
    const container = await render(<PurchaseCrosscheck shipmentId="SHP/1" />);
    expect(get).toHaveBeenCalledWith(ENDPOINT);
    expect(container.textContent).toContain('PO is shared by multiple shipments');
    expect(container.textContent).toContain('A posted PO receipt does not prove which shipment');
    expect(container.textContent).toContain('Received 1200 (all conditions)');
    expect(post).not.toHaveBeenCalled();
  });

  it('shows source failure as error, not a clean status', async () => {
    setRole('back_office');
    mockApi({ [ENDPOINT]: new Error('Stored purchasing records unavailable') });
    const container = await render(<PurchaseCrosscheck shipmentId="SHP/1" />);
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Stored purchasing records unavailable');
    expect(container.textContent).not.toContain('PO quantities agree');
  });

  it('rejects a malformed or falsely certified response', async () => {
    setRole('owner');
    mockApi({ [ENDPOINT]: {
      status: 'po_quantities_match_not_receipt_attribution', po_results: [],
      posted_receipts_prove_shipment_received: true,
      shipment_inventory_reconciled: true,
    } });
    const container = await render(<PurchaseCrosscheck shipmentId="SHP/1" />);
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('response is invalid');
  });

  it('does not fetch purchasing records for a sales representative', async () => {
    setRole('sales_rep');
    const { get } = mockApi({});
    const container = await render(<PurchaseCrosscheck shipmentId="SHP/1" />);
    expect(container.textContent).toBe('');
    expect(get).not.toHaveBeenCalled();
  });
});
