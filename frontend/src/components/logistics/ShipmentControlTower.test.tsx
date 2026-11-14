
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { ShipmentControlTower } from './ShipmentControlTower';
import { button, cleanup, click, mockApi, render, setRole } from './logisticsTestHarness';

afterEach(cleanup);

const shipment = {
  shipment_id: 'SHP-TEST-1', current_stage_label: 'Container & carrier movement',
  container_numbers: ['MSCU1234566'], bol_number: 'BOL-TEST-1', po_numbers: ['PO-TEST-1'],
  destination: 'New York', open_exceptions: 1, pending_evidence: 2,
  eta: { current: { eta: '2026-11-15', source: 'carrier_api', verified: true }, delay_days: 5 },
};
const detail = {
  ...shipment,
  stages: [{ stage: '06_carrier_movement', order: 6, label: 'Container & carrier movement',
    status: 'in_progress', responsible_party: 'carrier', expected_date: '2026-11-10',
    actual_date: null, verified_milestones: [
      { label: 'Departed origin', source: 'carrier_api', occurred_at: '2026-09-21', verified: true },
    ], unverified_reports: [{ source: 'email_parsed', occurred_at: '2026-11-14' }],
    exceptions: [{ code: 'SHIPMENT_DELAYED', message: 'ETA slipped' }] }],
  exceptions: [{ code: 'SHIPMENT_DELAYED', message: 'ETA slipped' }],
};

describe('shipment control tower evidence handling', () => {
  it('displays verified carrier ETA and unverified reports distinctly', async () => {
    setRole('manager');
    const { get, post } = mockApi({
      '/api/logistics/journey/shipments': { count: 1, shipments: [shipment] },
      '/api/logistics/monitor': { count: 1, alerts: [
        { code: 'SHIPMENT_DELAYED', severity: 'high', message: 'Five day delay', shipment_id: 'SHP-TEST-1' },
      ] },
      '/api/logistics/journey/shipments/SHP-TEST-1': detail,
    });
    const container = await render(<ShipmentControlTower />);
    expect(container.textContent).toContain('Verified ETA: 2026-11-15');
    expect(container.textContent).toContain('Unverified reports');
    expect(container.textContent).toContain('Five day delay');

    await click(button(container, 'MSCU1234566Container & carrier movementVerified ETA: 2026-11-15 · Exceptions: 1 · Evidence to review: 2'));
    expect(get).toHaveBeenCalledWith('/api/logistics/journey/shipments/SHP-TEST-1');
    expect(container.textContent).toContain('Departed origin · carrier_api');
    expect(container.textContent).toContain('unverified report(s) awaiting human review');
    expect(post).not.toHaveBeenCalled();
  });

  it('does not interpret a genuinely empty event store as no real-world shipments', async () => {
    setRole('back_office');
    mockApi({
      '/api/logistics/journey/shipments': { count: 0, shipments: [] },
      '/api/logistics/monitor': { count: 0, alerts: [] },
    });
    const container = await render(<ShipmentControlTower />);
    expect(container.textContent).toContain('This does not mean no shipments exist');
    expect(container.textContent).toContain('not a live carrier or customs all-clear');
  });

  it('fails closed on an unavailable ledger instead of rendering zero shipments', async () => {
    setRole('manager');
    mockApi({
      '/api/logistics/journey/shipments': new Error('Ledger offline'),
      '/api/logistics/monitor': new Error('Monitor offline'),
    });
    const container = await render(<ShipmentControlTower />);
    expect(container.querySelectorAll('[role="alert"]')).toHaveLength(2);
    expect(container.textContent).toContain('Shipment records unavailable: Ledger offline');
    expect(container.textContent).not.toContain('No shipment journeys are recorded');
  });
});
