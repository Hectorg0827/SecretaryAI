// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  canViewPricing: true,
  listSourcePrices: vi.fn(),
  previewReadiness: vi.fn(),
}));
vi.mock('../../lib/api', () => ({
  api: { pricePosting: {
    listSourcePrices: mocks.listSourcePrices,
    previewReadiness: mocks.previewReadiness,
  } },
}));
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    hasPermission: (permission: string) => permission === 'view_pricing' && mocks.canViewPricing,
  }),
}));

import { PricePostingReadinessPanel } from './PricePostingReadinessPanel';

const programs = [{
  id: 'synthetic-rule-id', program_key: 'NJ_ECPL', region_code: 'NJ',
  version: 1, program_name: 'Synthetic Current Price List',
  country_code: 'US', authority_title: 'NJ ABC',
  authority_url: 'https://www.nj.gov/',
  applicability_note: 'Candidate only',
  official_template_url: null, filing_deadline_rule: null,
  human_verified_for_tenant: false, submission_permitted: false as const,
}];

const source = {
  company_id: 'synthetic-tenant',
  items: [{
    id: 'synthetic-price-book-id', sku: 'SYN-001', market_code: 'NJ',
    package_label: '12x750', currency: 'USD', list_price_per_case: '60.000000',
    effective_from: '2026-11-01', effective_to: null, revision: 1,
    status: 'draft', cost_snapshot_id: null, evidence_ref: '', approved_at: null,
    channel: 'retail',
  }],
  status: 'analysis_only', source: 'connected_data',
  price_changes_allowed: false, cost_source_certified: false,
  count_returned: 1, next_offset: null,
};

let host: HTMLDivElement | null = null;
let root: Root | null = null;

async function flush() {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await Promise.resolve(); });
  }
}
async function render() {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root?.render(<PricePostingReadinessPanel programs={programs} />); });
  await flush();
  return host;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.canViewPricing = true;
  mocks.listSourcePrices.mockResolvedValue(source);
});

afterEach(async () => {
  if (root) await act(async () => { root?.unmount(); });
  root = null;
  host?.remove();
  host = null;
});

describe('read-only regulatory pricebook source reconciliation', () => {
  it('does not query pricing source without view_pricing permission', async () => {
    mocks.canViewPricing = false;
    const page = await render();
    expect(page.textContent).toContain('restricted to authorized Pricing users');
    expect(mocks.listSourcePrices).not.toHaveBeenCalled();
    expect(mocks.previewReadiness).not.toHaveBeenCalled();
  });

  it('shows an unavailable source as a failure, never clean or filed', async () => {
    mocks.listSourcePrices.mockRejectedValue(new Error('synthetic cost source outage'));
    const page = await render();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('synthetic cost source outage');
    expect(page.textContent).not.toContain('No price-book source revisions were returned');
    expect(mocks.previewReadiness).not.toHaveBeenCalled();
  });

  it('requires selected revision, rule and date before checking', async () => {
    const page = await render();
    expect(page.textContent).toContain('SYN-001');
    expect(page.textContent).toContain('Filing disabled');
    const action = Array.from(page.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('Check internal evidence'));
    expect(action?.disabled).toBe(true);
    expect(page.textContent).not.toContain('Submit to regulator');
    expect(mocks.previewReadiness).not.toHaveBeenCalled();
  });

  it('rejects unsafe source responses rather than displaying clearance', async () => {
    mocks.listSourcePrices.mockResolvedValue({
      ...source, status: 'ready_to_publish', price_changes_allowed: true,
    });
    const page = await render();
    expect(page.querySelector('[role="alert"]')?.textContent)
      .toContain('Pricing evidence could not be confirmed as read-only');
    expect(mocks.previewReadiness).not.toHaveBeenCalled();
  });

  it('shows incomplete first page rather than implying full coverage', async () => {
    mocks.listSourcePrices.mockResolvedValue({ ...source, next_offset: 100 });
    const page = await render();
    expect(page.textContent).toContain('only the first 100 source records');
    expect(page.textContent).toContain('Further price-book pages were not assessed');
  });
});
