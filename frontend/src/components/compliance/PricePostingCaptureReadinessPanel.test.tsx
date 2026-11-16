// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  role: 'owner', view: true, previewCaptureReadiness: vi.fn(),
}));
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    role: mocks.role,
    hasPermission: (permission: string) =>
      permission === 'view_pricing' && mocks.view,
  }),
}));
vi.mock('../../lib/api', () => ({
  api: { pricePosting: { previewCaptureReadiness: mocks.previewCaptureReadiness } },
}));
import { PricePostingCaptureReadinessPanel } from './PricePostingCaptureReadinessPanel';

const result = {
  status: 'capture_prerequisites_only', provider_requested: 'qbo',
  effective_date: '2026-12-01',
  items: [{
    price_book_id: 'synthetic-book', sku: 'SYN-NJ',
    provider: 'qbo', candidate_binding_count: 1,
    candidate_provider_item_id_present: true, candidate_unit_basis: 'single_unit',
    candidate_units_per_case: 12, state: 'needs_human_source_mapping',
    blockers: ['provider_item_identity_not_independently_verified'],
  }],
  not_mapped: 0, ambiguous: 0,
  real_provider_connection_verified: false, source_items_observed_live: false,
  provider_api_called: false, item_identity_certified: false,
  case_pack_verified: false, accounting_reconciled: false,
  safe_to_capture_live: false, observation_saved: false,
  ready_to_file: false, submission_permitted: false, price_published: false,
};
let host: HTMLDivElement | null;
let root: Root | null;
async function flush() {
  for (let i = 0; i < 3; i += 1) await act(async () => { await Promise.resolve(); });
}
async function mount() {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(
      <PricePostingCaptureReadinessPanel
        selectedIds={['synthetic-book']} effectiveDate="2026-12-01" />,
    );
  });
  await flush();
  return host;
}
async function click(page: HTMLDivElement) {
  const button = page.querySelector('button');
  await act(async () => { button?.click(); });
  await flush();
}
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.role = 'owner'; mocks.view = true;
  mocks.previewCaptureReadiness.mockResolvedValue(result);
});
afterEach(async () => {
  if (root) await act(async () => { root?.unmount(); });
  root = null;
  host?.remove();
  host = null;
});

describe('QBO/QBD capture mapping prerequisites', () => {
  it('never reaches provider implicitly and emphasizes unverified mappings', async () => {
    const page = await mount();
    expect(page.textContent).toContain('Does not connect to QuickBooks');
    expect(mocks.previewCaptureReadiness).not.toHaveBeenCalled();
    await click(page);
    expect(mocks.previewCaptureReadiness).toHaveBeenCalledWith({
      provider: 'qbo', effective_date: '2026-12-01',
      price_book_ids: ['synthetic-book'],
    });
    expect(page.textContent).toContain('recorded, unverified');
    expect(page.textContent).toContain('Live capture is disabled');
    expect(page.textContent).toContain('provider item identity not independently verified');
  });
  it('hides accounting item mapping from pricing-only staff', async () => {
    mocks.role = 'pricing_billbacks';
    const page = await mount();
    expect(page.textContent).toBe('');
    expect(mocks.previewCaptureReadiness).not.toHaveBeenCalled();
  });
  it('refuses any server result claiming live connection or authorization', async () => {
    mocks.previewCaptureReadiness.mockResolvedValue({
      ...result, source_items_observed_live: true,
    });
    const page = await mount();
    await click(page);
    expect(page.querySelector('[role="alert"]')?.textContent)
      .toContain('not confirmed as non-authorizing');
    expect(page.textContent).not.toContain('recorded, unverified');
  });
  it('treats source outages as unavailable, never unmapped zero', async () => {
    mocks.previewCaptureReadiness.mockRejectedValue(new Error('Synthetic mapping outage'));
    const page = await mount();
    await click(page);
    expect(page.querySelector('[role="alert"]')?.textContent)
      .toContain('Synthetic mapping outage');
    expect(page.textContent).not.toContain('0 missing mappings');
  });
});
