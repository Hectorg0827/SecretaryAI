import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../../lib/api';
import { findInsight } from '../registry';
import SyncHealthInsight, { meta, type SyncHealthResponse } from './sync-health';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><SyncHealthInsight /></MemoryRouter>);
  });
  return container;
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
  vi.restoreAllMocks();
});

const ADVISORY = {
  status: 'analysis_only', source: 'connected_data', requires_human_review: true,
  actions_executed: false, generated_at: '2026-10-28T12:00:00+00:00',
} as const;

function response(overrides: Partial<SyncHealthResponse> = {}): SyncHealthResponse {
  return {
    ...ADVISORY,
    state: 'mismatches_found',
    has_run: true,
    last_checked_at: '2026-10-28T10:00:00+00:00',
    stale: false,
    stale_after_hours: 12,
    checks: [{
      check_type: 'inventory_reconciliation',
      label: 'Stock counts: QuickBooks vs warehouse system',
      systems_compared: ['QuickBooks', 'Warehouse system'],
      recorded_status: 'warning',
      state: 'mismatches_found',
      checked_at: '2026-10-28T10:00:00+00:00',
      mismatch_count: 1,
      mismatches: [{
        item: 'Synthetic Malta 12oz', severity: 'critical', difference_pct: 40,
        message: 'Synthetic Malta 12oz: QB shows 100 units, warehouse shows 60 units (40.0% variance)',
      }],
    }],
    history: [
      { check_type: 'inventory_reconciliation', label: 'Stock counts: QuickBooks vs warehouse system',
        state: 'mismatches_found', checked_at: '2026-10-28T10:00:00+00:00', mismatch_count: 1 },
      { check_type: 'inventory_reconciliation', label: 'Stock counts: QuickBooks vs warehouse system',
        state: 'in_sync', checked_at: '2026-10-28T06:00:00+00:00', mismatch_count: 0 },
    ],
    ...overrides,
  };
}

describe('sync health insight', () => {
  it('is registered for owners and managers only', () => {
    expect(meta.slug).toBe('sync-health');
    expect(meta.group).toBe('Operations');
    expect([...meta.roles].sort()).toEqual(['manager', 'owner']);
    expect(findInsight('sync-health', 'owner')).toBeDefined();
    expect(findInsight('sync-health', 'sales_rep')).toBeUndefined();
    expect(findInsight('sync-health', 'back_office')).toBeUndefined();
  });

  it('shows mismatches, when the check last ran and the history', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue(response());
    const page = await render();
    expect(get).toHaveBeenCalledWith('/api/insights/sync-health');
    const text = page.textContent ?? '';
    expect(text).toContain('Your connected systems disagree');
    expect(text).toContain('1 difference found in the latest check');
    expect(text).toContain('Last checked Oct 28, 2026');
    expect(text).toContain('Compares QuickBooks and Warehouse system');
    expect(text).toContain('Synthetic Malta 12oz');
    expect(text).toContain('warehouse shows 60 units');
    expect(text).toContain('40.0%');
    expect(text).toContain('Large gap');
    expect(text).toContain('Recent checks');
    expect(text).toContain('In agreement');
    expect(text).toContain('Analysis only.');
    expect(text).not.toContain('automatic checks may not be running');
  });

  it('says plainly when no check has run, without claiming everything is fine', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(response({
      state: 'never_run', has_run: false, last_checked_at: null, checks: [], history: [],
    }));
    const page = await render();
    const text = page.textContent ?? '';
    expect(text).toContain('No sync check has run yet');
    expect(text).toContain('we cannot tell whether your systems agree');
    expect(text).toContain('Never checked');
    expect(text).not.toContain('Your connected systems agree');
    expect(text).not.toContain('No differences found');
    expect(text).not.toContain('In agreement');
  });

  it('does not report agreement when nothing could be compared', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(response({
      state: 'not_compared',
      checks: [{ ...response().checks[0], state: 'not_compared', recorded_status: 'healthy', mismatch_count: 0, mismatches: [] }],
      history: [],
    }));
    const page = await render();
    expect(page.textContent).toContain('Nothing was compared in the last check');
    expect(page.textContent).toContain('agreement is not confirmed');
    expect(page.textContent).not.toContain('Your connected systems agree');
  });

  it('confirms agreement only for an in-sync comparison', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(response({
      state: 'in_sync',
      checks: [{ ...response().checks[0], state: 'in_sync', recorded_status: 'healthy', mismatch_count: 0, mismatches: [] }],
    }));
    const page = await render();
    expect(page.textContent).toContain('Your connected systems agree');
    expect(page.textContent).toContain('No differences found.');
  });

  it('warns when the last check is stale', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(response({ stale: true }));
    const page = await render();
    expect(page.textContent).toContain('more than 12 hours ago');
  });

  it('shows an unavailable history (503) as an error with retry, not as healthy', async () => {
    const get = vi.spyOn(api, 'get')
      .mockRejectedValueOnce(new Error('Sync check history is unavailable; no analysis was produced'))
      .mockResolvedValueOnce(response());
    const page = await render();
    const alert = page.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Sync check history is unavailable');
    expect(page.textContent).not.toContain('Your connected systems agree');
    expect(page.textContent).not.toContain('No sync check has run yet');

    const retry = [...page.querySelectorAll('button')].find((b) => b.textContent === 'Try again')!;
    await act(async () => { retry.click(); });
    expect(get).toHaveBeenCalledTimes(2);
    expect(page.textContent).toContain('Your connected systems disagree');
  });
});
