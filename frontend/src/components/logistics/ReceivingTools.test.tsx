import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { StockReconciliation } from './StockReconciliation';
import { BrokerEmailParser, LogisticsTools, SeasonalIndices } from './LogisticsTools';
import { button, cleanup, click, field, mockApi, render, setRole, type } from './logisticsTestHarness';

afterEach(cleanup);

describe('stock check against QuickBooks', () => {
  it('shows ledger buckets and never calls an unlinked item reconciled', async () => {
    setRole('owner');
    const { get } = mockApi({ '/api/logistics/reconciliation/stock/CASE%201': {
      sku: 'CASE 1', product_name: 'Cola 24pk', unit: 'case', ledger_record_count: 2,
      ledger_quantities: { available: 90, damaged: 6, quarantined: 4 }, ledger_total: 100,
      latest_ledger_update: '2026-10-20T09:00:00Z', qb_quantity: null, indicative_quantity_difference: null,
      state: 'missing_explicit_qb_mapping', reconciled: false, scope_warning: 'Ledger includes only onboarded movements.',
    } });
    const container = await render(<StockReconciliation />);
    await type(field(container, 'SKU'), 'CASE 1');
    await click(button(container, 'Check stock'));
    expect(get).toHaveBeenCalledWith('/api/logistics/reconciliation/stock/CASE%201');
    expect(container.textContent).toContain('Cola 24pk');
    expect(container.textContent).toContain('not linked to a QuickBooks item');
    expect(container.textContent).toContain('Not compared');
    expect(container.textContent).toContain('Ledger includes only onboarded movements.');
  });

  it('shows a failed comparison as an error, not zero stock', async () => {
    setRole('manager');
    mockApi({ '/api/logistics/reconciliation/stock/CASE-1': new Error('QuickBooks inventory unavailable') });
    const container = await render(<StockReconciliation />);
    await type(field(container, 'SKU'), 'CASE-1');
    await click(button(container, 'Check stock'));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('QuickBooks inventory unavailable');
    expect(container.textContent).not.toContain('SecretaryAI total');
  });

  it('is hidden from roles the server rejects', async () => {
    setRole('sales_rep');
    mockApi();
    const container = await render(<StockReconciliation />);
    expect(container.textContent).toBe('');
  });
});

describe('customs broker email reader', () => {
  it('sends the pasted email and labels the result as unverified', async () => {
    setRole('owner');
    const { post } = mockApi({}, { '/api/logistics/customs/parse-broker-email': {
      booking_id: 'BK-1', candidate_milestone: 'hold_placed',
      candidate_event: { milestone: 'hold_placed', hold_reason: 'Fda Hold', estimated_demurrage_accruing: true },
      message_verified: false, requires_human_review: true,
    } });
    const container = await render(<BrokerEmailParser />);
    await type(field(container, 'Shipment booking reference'), ' BK-1 ');
    await type(field(container, 'Email subject'), 'Container exam');
    await type(field(container, 'Email text'), 'FDA hold placed on your container');
    await click(button(container, 'Read email'));
    expect(post).toHaveBeenCalledWith('/api/logistics/customs/parse-broker-email', {
      booking_id: 'BK-1', subject: 'Container exam', body: 'FDA hold placed on your container',
    });
    expect(container.textContent).toContain('Possible customs step: Held by customs');
    expect(container.textContent).toContain('Fda Hold');
    expect(container.textContent).toContain('have not been verified');
  });

  it('shows a configuration outage as an error', async () => {
    setRole('owner');
    mockApi({}, { '/api/logistics/customs/parse-broker-email': new Error('Company configuration unavailable') });
    const container = await render(<BrokerEmailParser />);
    await type(field(container, 'Shipment booking reference'), 'BK-1');
    await type(field(container, 'Email text'), 'released');
    await click(button(container, 'Read email'));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Company configuration unavailable');
  });
});

describe('seasonal demand pattern', () => {
  it('parses daily sales lines into the request and shows week multipliers', async () => {
    setRole('owner');
    const indices = Array.from({ length: 52 }, (_, i) => (i === 3 ? 1.4 : 1));
    const weeks = Array.from({ length: 52 }, (_, i) => `2026-0${1 + Math.floor(i / 30)}-0${1 + (i % 9)}`);
    const { post } = mockApi({}, { '/api/logistics/learning/seasonal-indices': {
      sku: 'CASE-1', modeled_data_quality: 'provisional', days_available: 200, min_days_needed: 182,
      data_months: 6.6, indices, week_start_dates: weeks,
    } });
    const container = await render(<SeasonalIndices />);
    await type(field(container, 'SKU'), 'CASE-1');
    await type(field(container, 'Daily sales'), 'date,units\n2026-01-01, 12\n2026-01-02,0\n2026-01-03\t3.5');
    await click(button(container, 'Calculate pattern'));
    expect(post).toHaveBeenCalledWith('/api/logistics/learning/seasonal-indices', {
      sku: 'CASE-1',
      daily_sales: [{ date: '2026-01-01', units: 12 }, { date: '2026-01-02', units: 0 }, { date: '2026-01-03', units: 3.5 }],
    });
    expect(container.textContent).toContain('Early estimate');
    expect(container.textContent).toContain('Busiest week: ×1.4');
  });

  it('says plainly when there is not enough history', async () => {
    setRole('owner');
    mockApi({}, { '/api/logistics/learning/seasonal-indices': {
      sku: 'CASE-1', modeled_data_quality: 'insufficient_data', days_available: 2, min_days_needed: 182,
      data_months: 0.1, indices: [], week_start_dates: [],
    } });
    const container = await render(<SeasonalIndices />);
    await type(field(container, 'SKU'), 'CASE-1');
    await type(field(container, 'Daily sales'), '2026-01-01, 12\n2026-01-02, 4');
    await click(button(container, 'Calculate pattern'));
    expect(container.textContent).toContain('at least 182 days of sales are needed');
    expect(container.textContent).not.toContain('Busiest week');
  });

  it('rejects malformed lines before sending', async () => {
    setRole('owner');
    const { post } = mockApi();
    const container = await render(<SeasonalIndices />);
    await type(field(container, 'SKU'), 'CASE-1');
    await type(field(container, 'Daily sales'), '2026-01-01, 12\nyesterday, 4');
    await click(button(container, 'Calculate pattern'));
    expect(container.textContent).toContain('Line 2 should look like');
    expect(post).not.toHaveBeenCalled();
  });

  it('hides the tools from viewers', async () => {
    setRole('viewer');
    mockApi();
    const container = await render(<LogisticsTools />);
    expect(container.textContent).toContain('not available for your role');
    expect(button(container, 'Read email')).toBeUndefined();
  });
});
