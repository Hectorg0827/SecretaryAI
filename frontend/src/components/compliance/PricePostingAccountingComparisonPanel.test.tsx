// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  compareAccounting: vi.fn(),
}));
vi.mock('../../lib/api', () => ({
  api: { pricePosting: { compareAccounting: mocks.compareAccounting } },
}));
import { PricePostingAccountingComparisonPanel } from './PricePostingAccountingComparisonPanel';

const output = {
  status: 'internal_numeric_comparison_only',
  checks: [{
    price_book_id: 'synthetic-price-id',
    sku: 'SYN-NJ',
    accounting_observation_id: 'synthetic-observation',
    provider: 'qbo',
    observed_amount_per_case: '71.250000',
    book_amount_per_case: '71.250000',
    numeric_comparison: 'numeric_match_unverified',
    blockers: [
      'synthetic_test_source_only',
      'accounting_connector_provenance_not_independently_authenticated',
    ],
  }],
  numeric_matches_unverified: 1,
  numeric_differences: 0,
  uncomparable: 0,
  independent_qbo_qbd_reconciliation_confirmed: false,
  warehouse_cost_coverage_verified: false,
  regulatory_requirements_verified: false,
  qbo_qbd_live_data_accessed: false,
  ready_to_file: false,
  submission_permitted: false,
  agency_template_generated: false,
  live_price_changed: false,
};

let host: HTMLDivElement | null = null;
let root: Root | null = null;
async function flush() {
  for (let i = 0; i < 4; i += 1)
    await act(async () => { await Promise.resolve(); });
}
async function mount(ids = ['synthetic-price-id']) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<PricePostingAccountingComparisonPanel selectedIds={ids} />);
  });
  await flush();
  return host;
}
async function clickCheck(page: HTMLElement) {
  const btn = Array.from(page.querySelectorAll('button'))
    .find((item) => item.textContent?.includes('Compare recorded observations'));
  expect(btn).toBeTruthy();
  await act(async () => btn?.click());
  await flush();
}
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.compareAccounting.mockResolvedValue(output);
});
afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  host?.remove();
  host = null;
});

describe('stored QBO/QBD observation comparisons', () => {
  it('shows explicit no-connection and unverified scope before checking', async () => {
    const page = await mount();
    expect(page.textContent).toContain('does not connect to QuickBooks');
    expect(page.textContent).toContain('does not');
    expect(mocks.compareAccounting).not.toHaveBeenCalled();
  });

  it('renders numeric agreement as unverified, never filing clearance', async () => {
    const page = await mount();
    await clickCheck(page);
    expect(mocks.compareAccounting).toHaveBeenCalledWith({
      price_book_ids: ['synthetic-price-id'],
    });
    expect(page.textContent).toContain('1 numeric matches (unverified)');
    expect(page.textContent).toContain('Independent accounting reconciliation: not established');
    expect(page.textContent).toContain('synthetic test source only');
    expect(page.textContent).toContain('Filing disabled');
  });

  it('refuses a response claiming a successful filing', async () => {
    mocks.compareAccounting.mockResolvedValue({
      ...output, submission_permitted: true,
    });
    const page = await mount();
    await clickCheck(page);
    expect(page.querySelector('[role="alert"]')?.textContent)
      .toContain('not confirmed as non-certifying');
    expect(page.textContent).not.toContain('1 numeric matches');
  });

  it('treats the accounting source outage as an error not zero', async () => {
    mocks.compareAccounting.mockRejectedValue(new Error('Synthetic observations unavailable'));
    const page = await mount();
    await clickCheck(page);
    expect(page.querySelector('[role="alert"]')?.textContent)
      .toContain('Synthetic observations unavailable');
    expect(page.textContent).not.toContain('numeric matches (unverified)');
  });

  it('disables comparison when no prices are selected', async () => {
    const page = await mount([]);
    expect(page.querySelector('button')?.disabled).toBe(true);
    expect(mocks.compareAccounting).not.toHaveBeenCalled();
  });
});
