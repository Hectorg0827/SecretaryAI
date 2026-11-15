import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('../../lib/api', () => ({ api: { get: mock.get, post: mock.post } }));
import { LolaReviewJournal } from './LolaReviewJournal';

const item = {
  id: '123e4567-e89b-12d3-a456-426614174080',
  source_alert_id: 'alr-test', shipment_ref: 'SHIP-01',
  alert_code: 'CUSTOMS_HOLD', severity: 'critical',
  evidence_quality: 'monitor_derived', source_revision: 'a'.repeat(64),
  review_state: 'pending', first_seen_at: '2026-11-15T03:00:00Z',
};
const data = {
  scope: 'recorded_logistics_reviews_only',
  state: 'recorded_reviews', items: [item], next_offset: null,
  review_does_not_approve_external_actions: true,
  external_writes_executed: false,
};
let root: Root | null = null;
let mount: HTMLDivElement | null = null;
async function render(enabled = true) {
  mount = document.createElement('div');
  document.body.appendChild(mount);
  root = createRoot(mount);
  await act(async () => {
    root?.render(<MemoryRouter><LolaReviewJournal enabled={enabled} /></MemoryRouter>);
  });
  return mount;
}
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mock.get.mockResolvedValue(data);
  mock.post.mockImplementation(async (path: string) => path.includes('/capture')
    ? { state: 'capture_completed', candidate_records: 1,
        external_writes_executed: false, approval_created: false }
    : { review_id: item.id, review_state: 'acknowledged',
        external_writes_executed: false });
});
afterEach(async () => {
  if (root) await act(async () => { root?.unmount(); });
  mount?.remove();
  root = null;
  mount = null;
});
describe('Lola durable review journal (synthetic state only)', () => {
  it('never loads a restricted role', async () => {
    const node = await render(false);
    expect(node.textContent).toBe('');
    expect(mock.get).not.toHaveBeenCalled();
    expect(mock.post).not.toHaveBeenCalled();
  });
  it('presents logged alerts without any business action approval control', async () => {
    const node = await render();
    expect(node.textContent).toContain('CUSTOMS HOLD');
    expect(node.textContent).toContain('Shipment SHIP-01');
    expect(node.textContent).toContain('Pending review');
    expect(node.textContent).toContain('never authorizes');
    expect(node.textContent).not.toContain('Approve payment');
    expect(mock.post).not.toHaveBeenCalled();
  });
  it('requires an explicit user click to manually capture', async () => {
    const node = await render();
    const btn = [...node.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('Record current exceptions'));
    await act(async () => { btn?.click(); });
    expect(mock.post).toHaveBeenCalledWith(
      '/api/lola/chief-of-staff/reviews/capture', {});
    expect(node.textContent).toContain('No actions performed');
  });
  it('acknowledges the precise revision, never approving downstream changes', async () => {
    const node = await render();
    const btn = [...node.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('Acknowledge observation'));
    await act(async () => { btn?.click(); });
    expect(mock.post).toHaveBeenCalledWith(
      '/api/lola/chief-of-staff/reviews/' + item.id + '/acknowledge',
      { expected_source_revision: item.source_revision },
    );
    expect(node.textContent).toContain('no business action was approved');
  });
  it('fails closed when source response claims execution', async () => {
    mock.get.mockResolvedValue({ ...data, external_writes_executed: true });
    const node = await render();
    expect(node.textContent).toContain('Review history unavailable');
    expect(node.textContent).not.toContain('CUSTOMS HOLD');
  });
  it('does not claim a successful capture after an uncertain response', async () => {
    mock.post.mockResolvedValue({ state: 'capture_completed' });
    const node = await render();
    const btn = [...node.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('Record current exceptions'));
    await act(async () => { btn?.click(); });
    expect(node.textContent).toContain('No successful persistence is assumed');
  });
});
