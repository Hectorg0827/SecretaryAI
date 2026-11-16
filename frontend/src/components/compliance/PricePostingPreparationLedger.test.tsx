// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  role: 'owner',
  view: true,
  list: vi.fn(),
  create: vi.fn(),
  listReferences: vi.fn(),
  addReference: vi.fn(),
  listSourcePrices: vi.fn(),
}));
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    role: mocks.role,
    hasPermission: (value: string) => value === 'view_pricing' && mocks.view,
  }),
}));
vi.mock('../../lib/api', () => ({
  api: {
    pricePreparations: {
      list: mocks.list,
      create: mocks.create,
      listReferences: mocks.listReferences,
      addReference: mocks.addReference,
    },
    pricePosting: { listSourcePrices: mocks.listSourcePrices },
  },
}));
import { PricePostingPreparationLedger } from './PricePostingPreparationLedger';

const programs = [{
  id: 'rule-synthetic', program_key: 'NJ_ECPL', version: 1,
  country_code: 'US', region_code: 'NJ',
  program_name: 'Synthetic current list',
  authority_title: 'Source', authority_url: 'https://www.nj.gov/',
  applicability_note: 'Candidate only',
  official_template_url: null, filing_deadline_rule: null,
  human_verified_for_tenant: true, submission_permitted: false as const,
}];

const pricebook = {
  status: 'analysis_only', source: 'connected_data',
  price_changes_allowed: false, cost_source_certified: false,
  items: [{
    id: 'price-synthetic', sku: 'SYN-001', market_code: 'NJ',
    revision: 1, status: 'draft', package_label: '12x750',
    list_price_per_case: '60.000000', currency: 'USD',
    effective_from: '2026-12-01',
  }],
};
const packet = {
  id: 'case-synthetic', company_id: 'tenant-synthetic',
  rule_id: 'rule-synthetic', rule_version: 1,
  created_by: 'human-owner', effective_date: '2026-12-01',
  price_book_snapshot: [{
    price_book_id: 'price-synthetic', sku: 'SYN-001',
    list_price_per_case: '60.000000', status: 'draft', revision: 1,
    market_code: 'NJ',
  }],
  source_fingerprint: '0'.repeat(32),
  state: 'internal_preparation_only', created_at: '2026-11-15T20:00:00Z',
};

let host: HTMLDivElement | null = null;
let root: Root | null = null;
async function flush() {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await Promise.resolve(); });
  }
}
async function mount() {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root?.render(<PricePostingPreparationLedger programs={programs} />); });
  await flush();
  return host;
}
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.role = 'owner';
  mocks.view = true;
  mocks.list.mockResolvedValue({
    items: [packet], next_offset: null,
    submission_permitted: false, agency_confirmation: false,
  });
  mocks.listSourcePrices.mockResolvedValue(pricebook);
  mocks.listReferences.mockResolvedValue({
    items: [], agency_confirmation: false, submission_permitted: false,
  });
});
afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  host?.remove();
  host = null;
});

describe('internal price preparation register', () => {
  it('labels frozen cases not filed and requires an acknowledgement', async () => {
    const page = await mount();
    expect(page.textContent).toContain('Internal preparation only — never submitted or confirmed');
    expect(page.textContent).toContain('not prove submission or acceptance');
    const save = Array.from(page.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('Create internal case'));
    expect(save?.disabled).toBe(true);
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it('never loads sensitive source when Pricing read permission is missing', async () => {
    mocks.view = false;
    const page = await mount();
    expect(page.textContent).toContain('require Pricing read authorization');
    expect(mocks.list).not.toHaveBeenCalled();
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it('shows the register to pricing staff but not write controls', async () => {
    mocks.role = 'pricing_billbacks';
    const page = await mount();
    expect(page.textContent).toContain('case-synthetic');
    expect(page.textContent).not.toContain('Create internal case');
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it('treats source failure as unavailable rather than an empty register', async () => {
    mocks.list.mockRejectedValue(new Error('synthetic ledger outage'));
    const page = await mount();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('synthetic ledger outage');
    expect(page.textContent).not.toContain('No internal preparations returned');
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it('rejects a source that claims it permits submission', async () => {
    mocks.list.mockResolvedValue({
      items: [packet], next_offset: null, submission_permitted: true,
      agency_confirmation: false,
    });
    const page = await mount();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Internal source could not be validated');
    expect(mocks.create).not.toHaveBeenCalled();
  });
});
