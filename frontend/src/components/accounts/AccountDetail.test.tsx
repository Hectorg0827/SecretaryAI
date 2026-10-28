import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, type Account } from '../../lib/api';
import { AccountDetail, type AccountOrdersResponse } from './AccountDetail';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function flush() {
  for (let i = 0; i < 5; i += 1) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  }
}

async function mount(node: ReactNode) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root?.render(node); });
  await flush();
  return container;
}

async function click(element: Element | null | undefined) {
  if (!element) throw new Error('Element to click was not found');
  await act(async () => { (element as HTMLElement).click(); });
  await flush();
}

function tab(scope: ParentNode, label: string) {
  return Array.from(scope.querySelectorAll('button')).find((b) => b.textContent?.trim() === label);
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
  localStorage.clear();
  vi.restoreAllMocks();
});

const ACCOUNT: Account & { avg_order_value_observed: boolean } = {
  id: 'QB 1',
  name: 'Fixture Grocers',
  email: '',
  phone: '',
  state: 'NY',
  health_status: 'healthy',
  health_score: 80,
  last_order_date: '2026-10-01',
  current_balance: 40,
  avg_order_value: 100,
  avg_order_value_observed: true,
  assigned_rep: null,
};

function ordersResponse(overrides: Partial<AccountOrdersResponse> = {}): AccountOrdersResponse {
  return {
    account_id: 'QB 1',
    orders: [
      { id: 'inv-new', invoice_date: '2026-10-25', due_date: '2026-11-24', total_amount: 1234.5, balance_due: 40, status: 'open' },
      { id: 'inv-old', invoice_date: '2026-03-02', due_date: null, total_amount: 99, balance_due: 0, status: 'paid' },
    ],
    count: 2,
    total: 2,
    truncated: false,
    history_window_days: 365,
    unmatched_invoices: 0,
    ...overrides,
  };
}

const ORDERS_PATH = '/api/accounts/QB%201/orders';

describe('account detail orders tab', () => {
  it('requests the account orders endpoint and lists invoices with amounts, dates and status', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue(ordersResponse());
    const page = await mount(<AccountDetail account={ACCOUNT} onClose={() => {}} />);
    expect(get).not.toHaveBeenCalled();

    await click(tab(page, 'orders'));
    expect(get).toHaveBeenCalledWith(ORDERS_PATH);
    const text = page.textContent ?? '';
    expect(text).toContain('2 invoices from the last 365 days.');
    expect(text).toContain('QuickBooks ref inv-new');
    expect(text).toContain('$1,234.50');
    // A date-only invoice date is the calendar day, not shifted by time zone.
    expect(text).toContain('Oct 25, 2026');
    expect(text).toContain('$40.00 still owed');
    expect(text).toContain('paid');
    expect(page.querySelector('[role="alert"]')).toBeNull();
  });

  it('shows a failed read as an error with retry, never as "no orders"', async () => {
    const get = vi.spyOn(api, 'get')
      .mockRejectedValueOnce(new Error('Order history is unavailable right now'))
      .mockResolvedValueOnce(ordersResponse());
    const page = await mount(<AccountDetail account={ACCOUNT} onClose={() => {}} />);
    await click(tab(page, 'orders'));

    const alert = page.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Orders could not be loaded: Order history is unavailable right now');
    expect(page.textContent).not.toContain('No invoices');
    expect(page.textContent).not.toContain('No orders');

    await click(tab(page, 'Try again'));
    expect(get).toHaveBeenCalledTimes(2);
    expect(page.querySelector('[role="alert"]')).toBeNull();
    expect(page.textContent).toContain('QuickBooks ref inv-new');
  });

  it('treats a malformed response as an error, not an empty list', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({ orders: null });
    const page = await mount(<AccountDetail account={ACCOUNT} onClose={() => {}} />);
    await click(tab(page, 'orders'));
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('incomplete');
  });

  it('says the empty list covers only the history window', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(ordersResponse({ orders: [], count: 0, total: 0 }));
    const page = await mount(<AccountDetail account={ACCOUNT} onClose={() => {}} />);
    await click(tab(page, 'orders'));
    expect(page.textContent).toContain('No invoices for this account in the last 365 days.');
  });

  it('says when only the newest invoices are shown and when some invoices could not be matched', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(ordersResponse({ total: 140, truncated: true, unmatched_invoices: 3 }));
    const page = await mount(<AccountDetail account={ACCOUNT} onClose={() => {}} />);
    await click(tab(page, 'orders'));
    const text = page.textContent ?? '';
    expect(text).toContain('Showing the newest 2 of 140 invoices from the last 365 days.');
    expect(text).toContain('3 invoices have no customer attached in QuickBooks, so this list may be missing some orders.');
  });
});

describe('account detail summary', () => {
  it('shows an unobserved average order as unknown, not $0', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(ordersResponse());
    const unobserved = { ...ACCOUNT, avg_order_value: 0, avg_order_value_observed: false };
    const page = await mount(<AccountDetail account={unobserved} onClose={() => {}} />);
    const avg = Array.from(page.querySelectorAll('p')).find((p) => p.textContent === 'Avg Order');
    expect(avg?.previousElementSibling?.textContent).toBe('—');
  });
});
