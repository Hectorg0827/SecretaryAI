import { afterEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../../lib/api';
import Receivables, { CURRENCY_KEY, meta, type ReceivablesResponse } from './receivables';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><Receivables /></MemoryRouter>);
  });
  await act(async () => {});
  return container;
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
  vi.restoreAllMocks();
  localStorage.clear();
});

const ADVISORY = {
  status: 'analysis_only', source: 'connected_data', requires_human_review: true,
  actions_executed: false, generated_at: '2026-10-28T12:00:00Z',
} as const;

const zero = { current: 0, days_1_30: 0, days_31_60: 0, days_61_90: 0, days_91_plus: 0, unaged: 0 };

function response(currency: string): ReceivablesResponse {
  return {
    ...ADVISORY,
    as_of: '2026-10-28',
    currency,
    aging_status: 'partial_aging',
    exceptions: ['missing_due_date'],
    total_owed: 2800,
    overdue_total: 1600,
    buckets: { current: 1000, days_1_30: 300, days_31_60: 800, days_61_90: 500, days_91_plus: 0, unaged: 200 },
    open_invoice_count: 5,
    accounts_total: 3,
    accounts: [
      {
        customer_id: 'QB1', name: 'Acme Corp', total_owed: 1500, overdue: 500,
        buckets: { ...zero, current: 1000, days_61_90: 500 }, open_invoices: 2, oldest_days_overdue: 75,
        priority: 'high', next_step: 'Call this week about the invoices more than 60 days overdue.',
        quickbooks_balance: 1500, balance_matches_quickbooks: true,
      },
      {
        customer_id: 'QB3', name: 'Corner Deli', total_owed: 300, overdue: 300,
        buckets: { ...zero, days_1_30: 300 }, open_invoices: 1, oldest_days_overdue: 5,
        priority: 'low', next_step: 'Send a friendly payment reminder.',
        quickbooks_balance: 250, balance_matches_quickbooks: false,
      },
      {
        customer_id: 'QB7', name: 'Granite Co', total_owed: 200, overdue: 0,
        buckets: { ...zero, unaged: 200 }, open_invoices: 1, oldest_days_overdue: null,
        priority: null, next_step: 'Some invoices have no due date in QuickBooks; check their payment terms.',
        quickbooks_balance: 200, balance_matches_quickbooks: true,
      },
    ],
    not_aged: [
      {
        customer_id: 'QB5', name: 'Echo Foods', quickbooks_balance: 900,
        reason: "Some of this customer's invoices could not be read, so their balance was not aged.",
      },
    ],
    truncated: false,
    rows_skipped: 2,
    rows_unattributed: 1,
  };
}

describe('receivables insight', () => {
  it('is offered only to roles the backend admits (view_financials)', () => {
    expect([...meta.roles].sort()).toEqual(['manager', 'owner']);
    expect(meta.group).toBe('Finance');
  });

  it('asks for the currency first and requests aging in the confirmed currency', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue(response('CAD'));
    const page = await render();

    expect(get).not.toHaveBeenCalled();
    expect(page.textContent).toContain('Which currency are your QuickBooks invoices in?');

    const select = page.querySelector('select') as HTMLSelectElement;
    await act(async () => {
      select.value = 'CAD';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => {
      page.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    await act(async () => {});

    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith('/api/insights/receivables?currency=CAD');
    expect(localStorage.getItem(CURRENCY_KEY)).toBe('CAD');
    expect(page.textContent).toContain('Amounts in CAD');
    expect(page.textContent).toContain('CA$2,800.00');
  });

  it('shows totals, aging and who to follow up with first', async () => {
    localStorage.setItem(CURRENCY_KEY, 'USD');
    const get = vi.spyOn(api, 'get').mockResolvedValue(response('USD'));
    const page = await render();

    expect(get).toHaveBeenCalledWith('/api/insights/receivables?currency=USD');
    const text = page.textContent ?? '';
    expect(text).toContain('Analysis only.');
    expect(text).toContain('$2,800.00');
    expect(text).toContain('$1,600.00');
    expect(text).toContain('61–90 days overdue');
    expect(text).toContain('Call first');
    expect(text).toContain('75 days');
    expect(text).toContain('QuickBooks balance is $250.00');
    expect(text).toContain('Owing, but nothing overdue');
    expect(text).toContain('Granite Co');
    expect(text).toContain('Could not be checked');
    expect(text).toContain('Echo Foods');
    expect(text).toContain('No due date');
    expect(text).toContain('1 invoice(s) could not be read and had no customer attached');
    expect(text).toContain('1 invoice(s) could not be read; their customers');
    const rows = page.querySelectorAll('section[aria-label="Who to follow up with first"] tbody tr');
    expect([...rows].map((row) => row.querySelector('td')?.textContent)).toEqual([
      'Acme Corp', expect.stringContaining('Corner Deli'),
    ]);
  });

  it('shows no totals when the aging could not be worked out', async () => {
    localStorage.setItem(CURRENCY_KEY, 'USD');
    vi.spyOn(api, 'get').mockResolvedValue({
      ...ADVISORY, as_of: '2026-10-28', currency: 'USD', aging_status: 'unavailable',
      exceptions: ['source_stale'], total_owed: null, overdue_total: null, buckets: null,
    });
    const page = await render();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('the QuickBooks data is out of date');
    expect(page.textContent).not.toContain('Total owed to you');
  });

  it('shows an error state when the source is down', async () => {
    localStorage.setItem(CURRENCY_KEY, 'USD');
    vi.spyOn(api, 'get').mockRejectedValue(new Error('Invoices is unavailable; no analysis was produced'));
    const page = await render();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Invoices is unavailable');
  });
});
