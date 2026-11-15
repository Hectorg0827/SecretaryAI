import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../../lib/api', () => ({ api: { get: mock.get } }));
import { LolaPricingEvidence } from './LolaPricingEvidence';

const SNAP = '123e4567-e89b-12d3-a456-426614174001';
const candidates = {
  state: 'snapshot_candidates',
  items: [{
    cost_snapshot_id: SNAP, sku: 'TEST-SKU', shipment_ref: 'SHIP-TEST',
    source_cost_as_of: '2026-11-14', source_evidence_status: 'provisional',
  }],
  has_more: false, accounting_reconciled: false, price_changes_allowed: false,
};
const review = {
  cost_snapshot_id: SNAP, currency: 'USD',
  recorded_cost_per_case: '22.000000', source_documentation_complete: false,
  issues_requiring_review: ['missing_warehouse_cost_categories'],
  accounting_reconciled: false, gross_margin_calculated: false,
  price_publication_verified: false, external_writes_executed: false,
};
let root: Root | null = null;
let mount: HTMLDivElement | null = null;

async function render(enabled = true) {
  mount = document.createElement('div');
  document.body.appendChild(mount);
  root = createRoot(mount);
  await act(async () => {
    root?.render(<MemoryRouter><LolaPricingEvidence enabled={enabled} /></MemoryRouter>);
  });
  return mount;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mock.get.mockImplementation(async (path: string) =>
    path.includes('?limit=') ? candidates : review);
});
afterEach(async () => {
  if (root) await act(async () => { root?.unmount(); });
  mount?.remove(); mount = null; root = null;
});

describe('Lola pricing: synthetic, source-only evidence', () => {
  it('does not fetch data for a non-authorized page role', async () => {
    const node = await render(false);
    expect(node.textContent).toBe('');
    expect(mock.get).not.toHaveBeenCalled();
  });

  it('shows snapshot evidence without suggesting an executable price action', async () => {
    const node = await render();
    expect(node.textContent).toContain('TEST-SKU');
    expect(node.textContent).toContain('Provisional source');
    const button = [...node.querySelectorAll('button')].find((b) => b.textContent?.includes('Inspect evidence'));
    await act(async () => { button?.click(); });
    expect(node.textContent).toContain('USD 22.000000');
    expect(node.textContent).toContain('Missing warehouse costs');
    expect(node.textContent).toContain('No verified gross margin');
    expect(node.textContent).not.toContain('Approve price');
    expect(mock.get).toHaveBeenCalledTimes(2);
  });

  it('rejects malformed API data instead of rendering fabricated zeroes', async () => {
    mock.get.mockResolvedValue({});
    const node = await render();
    expect(node.textContent).toContain('Pricing evidence is unavailable');
    expect(node.textContent).not.toContain('USD 0');
  });

  it('rejects a review that falsely claims accounting reconciliation', async () => {
    mock.get.mockImplementation(async (path: string) =>
      path.includes('?limit=') ? candidates : { ...review, accounting_reconciled: true });
    const node = await render();
    await act(async () => { node.querySelector('button[aria-expanded="false"]')?.dispatchEvent(
      new MouseEvent('click', { bubbles: true })
    ); });
    expect(node.textContent).toContain('Evidence unavailable or inconsistent');
    expect(node.textContent).not.toContain('USD 22.000000');
  });
});
