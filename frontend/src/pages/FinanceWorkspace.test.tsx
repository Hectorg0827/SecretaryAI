import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../lib/api', () => ({ api: { get: mocks.get } }));

import { FinanceWorkspace } from './FinanceWorkspace';

let root: Root | undefined;
let container: HTMLDivElement | undefined;
async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root?.render(<FinanceWorkspace />); });
  return container;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
});
afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
});

describe('finance workspace server-scoped rendering', () => {
  it('renders only returned AR scope without AP, ledger or customer identity', async () => {
    mocks.get.mockResolvedValue({
      role: 'ar_specialist', authorized_scopes: ['ar'],
      snapshot_generated_at: '2026-11-10T05:00:00Z', source_quality: 'partial',
      currency_verified: false, ledger_reconciled: false,
      sections: { ar: { status: 'observed', headers_examined: 7, open_count: 2,
        overdue_count: 1, due_date_missing_count: 0, variance_detected: null,
        note: 'Sandbox counts only' } },
    });
    const node = await render();
    expect(mocks.get).toHaveBeenCalledWith('/finance/workspace');
    expect(node.textContent).toContain('Accounts Receivable');
    expect(node.textContent).toContain('Open');
    expect(node.textContent).not.toContain('Accounts Payable');
    expect(node.textContent).not.toContain('Pricing & Billbacks');
    expect(node.textContent).not.toContain('Customer');
  });

  it('shows unavailable, never a zero balance, on a denied or failed read', async () => {
    mocks.get.mockRejectedValue(new Error('403'));
    const node = await render();
    expect(node.querySelector('[role="alert"]')?.textContent).toContain('unavailable');
    expect(node.textContent).not.toContain('Open: 0');
  });
});
