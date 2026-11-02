import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { WarehousePreviews } from './WarehousePreviews';
import { ReturnPreview } from './ReturnPreview';
import { byLabel, button, check, cleanup, click, field, mockApi, render, setRole, type } from './logisticsTestHarness';

const BASE_RESULT = {
  status: 'analysis_only', snapshot_stale: false, snapshot_age_minutes: 1, warehouse_snapshot_verified: false,
};

afterEach(cleanup);

describe('warehouse checks', () => {
  it('sends a pick plan with typed numbers and a timezone-aware time, and shows picks', async () => {
    setRole('manager');
    const { post } = mockApi({}, { '/api/logistics/warehouse/pick-plan': {
      ...BASE_RESULT, picks: [{ sku: 'CASE-1', bin_id: 'A1', lot_code: 'L1', cases: 5 }],
      shortages: [{ sku: 'CASE-1', missing_cases: 2 }], excluded_expired_observations: 0,
    } });
    const container = await render(<WarehousePreviews />);
    await type(field(container, 'Warehouse'), 'MAIN');
    await type(field(container, 'Counts taken at'), '2026-10-20T09:30');
    await type(field(container, 'Order reference'), 'SO-9');
    await type(byLabel(container, 'Order lines row 1 SKU'), 'CASE-1');
    await type(byLabel(container, 'Order lines row 1 Cases'), '7');
    await check(byLabel(container, 'Order lines row 1 Needs expiry date'));
    await type(byLabel(container, 'Bin counts row 1 Bin'), 'A1');
    await type(byLabel(container, 'Bin counts row 1 SKU'), 'CASE-1');
    await type(byLabel(container, 'Bin counts row 1 Lot'), 'L1');
    await type(byLabel(container, 'Bin counts row 1 On hand'), '5');
    await type(byLabel(container, 'Bin counts row 1 Expiry'), '2027-01-31');
    await click(button(container, 'Show preview'));
    expect(post).toHaveBeenCalledWith('/api/logistics/warehouse/pick-plan', {
      warehouse_id: 'MAIN',
      observed_at: new Date('2026-10-20T09:30').toISOString(),
      unit: 'case',
      order_reference: 'SO-9',
      lines: [{ sku: 'CASE-1', requested_cases: 7, require_expiry: true }],
      bins: [{
        bin_id: 'A1', sku: 'CASE-1', lot_code: 'L1', on_hand_cases: 5, allocated_cases: 0,
        quarantined_cases: 0, expiry_date: '2027-01-31', travel_steps: 0,
      }],
    });
    expect(container.textContent).toContain('Short 2 cases of CASE-1');
    expect(container.textContent).toContain('No stock was reserved');
  });

  it('shows capacity results and server validation errors', async () => {
    setRole('owner');
    const { postRoutes } = mockApi({}, { '/api/logistics/warehouse/capacity': {
      ...BASE_RESULT, total_capacity_cases: 100, occupied_cases: 80, reserved_cases: 0,
      physical_utilization_pct: 80, overcommitted_cases: 0, slotting_confidence: 'insufficient_data', candidate_moves: [],
    } });
    const container = await render(<WarehousePreviews />);
    await click(button(container, 'Warehouse space'));
    await type(field(container, 'Warehouse'), 'MAIN');
    await type(byLabel(container, 'Bins row 1 Bin'), 'A1');
    await type(byLabel(container, 'Bins row 1 Capacity'), '100');
    await type(byLabel(container, 'Bins row 1 Occupied'), '80');
    await click(button(container, 'Show preview'));
    expect(container.textContent).toContain('80%');
    expect(container.textContent).toContain('Not enough picks counted');

    postRoutes['/api/logistics/warehouse/capacity'] = new Error('Warehouse observation cannot be in the future');
    await click(button(container, 'Show preview'));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Warehouse observation cannot be in the future');
  });

  it('rejects non-numeric counts before sending', async () => {
    setRole('owner');
    const { post } = mockApi();
    const container = await render(<WarehousePreviews />);
    await click(button(container, 'Cycle count check'));
    await type(field(container, 'Warehouse'), 'MAIN');
    await type(field(container, 'Count sheet reference'), 'CC-1');
    await type(byLabel(container, 'Count lines row 1 SKU'), 'CASE-1');
    await type(byLabel(container, 'Count lines row 1 Bin'), 'A1');
    await type(byLabel(container, 'Count lines row 1 Expected'), 'ten');
    await type(byLabel(container, 'Count lines row 1 Counted'), '9');
    await click(button(container, 'Show preview'));
    expect(container.textContent).toContain('Count lines, row 1, Expected: enter a whole number');
    expect(post).not.toHaveBeenCalled();
  });

  it('only offers sales staff the checks their role allows', async () => {
    setRole('sales_rep');
    mockApi();
    const container = await render(<WarehousePreviews />);
    expect(button(container, 'Warehouse space')).toBeDefined();
    expect(button(container, 'Pick plan')).toBeUndefined();
    expect(button(container, 'Supplier claim draft')).toBeUndefined();
  });
});

describe('return review', () => {
  async function fill(container: HTMLElement) {
    await type(field(container, 'Return reference'), 'RMA-1');
    await type(field(container, 'Original order reference'), 'SO-9');
    await type(field(container, 'Customer'), 'CUST-1');
    await type(field(container, 'Supplier'), 'SUP-1');
    await type(field(container, 'SKU'), 'CASE-1');
    await type(field(container, 'Warehouse'), 'MAIN');
    await type(field(container, 'Cases being returned'), '3');
    await type(field(container, 'Product type'), 'beverage');
    await type(field(container, 'Reported at'), '2026-10-20T09:30');
    await type(field(container, 'Reason for the return'), 'Cans arrived dented');
  }

  it('sends the return and shows the suggested classification and next steps', async () => {
    setRole('back_office');
    const { post } = mockApi({}, { '/api/logistics/returns/preview': {
      return_reference: 'RMA-1', sku: 'CASE-1', requested_cases: 3, category_candidate: 'damaged_in_transit',
      classification_verified: false, proposed_disposition: 'hold_for_inspection',
      inspection_routing: 'quarantine_review_and_independent_quality_inspection', snapshot_stale: false,
      snapshot_age_minutes: 2, next_action: 'verify', disposition_authorized: false, inventory_posted: false,
      credit_issued: false, supplier_claim_filed: false,
      pipeline: [{ stage: 'return_request', state: 'unverified_caller_preview' }, { stage: 'disposition_approval', state: 'not_authorized' }],
    } });
    const container = await render(<ReturnPreview />);
    await fill(container);
    await click(button(container, 'Review return'));
    expect(post).toHaveBeenCalledWith('/api/logistics/returns/preview', {
      return_reference: 'RMA-1', order_reference: 'SO-9', warehouse_id: 'MAIN', sku: 'CASE-1',
      customer_id: 'CUST-1', supplier_id: 'SUP-1', lot_code: '', requested_cases: 3, unit: 'case',
      product_type: 'beverage', return_reason: 'Cans arrived dented',
      observed_at: new Date('2026-10-20T09:30').toISOString(),
    });
    expect(container.textContent).toContain('Damaged in transit');
    expect(container.textContent).toContain('Hold the goods for inspection');
    expect(container.textContent).toContain('Decision approved');
    expect(container.textContent).toContain('Not approved');
    expect(container.textContent).toContain('no credit was issued');
  });

  it('shows a server error clearly', async () => {
    setRole('owner');
    mockApi({}, { '/api/logistics/returns/preview': new Error('Return observation must not be in the future') });
    const container = await render(<ReturnPreview />);
    await fill(container);
    await click(button(container, 'Review return'));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Return observation must not be in the future');
  });

  it('is not offered to sales staff', async () => {
    setRole('sales_rep');
    mockApi();
    const container = await render(<ReturnPreview />);
    expect(button(container, 'Review return')).toBeUndefined();
  });
});
