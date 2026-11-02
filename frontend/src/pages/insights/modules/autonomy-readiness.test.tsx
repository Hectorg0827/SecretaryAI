import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../../lib/api';
import AutonomyReadinessPage, { meta, type AutonomyReadiness, type AutonomyRow } from './autonomy-readiness';

let root: Root | undefined;
let container: HTMLDivElement;

async function render() {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><AutonomyReadinessPage /></MemoryRouter>);
  });
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  vi.restoreAllMocks();
});

const row = (overrides: Partial<AutonomyRow>): AutonomyRow => ({
  action_type: 'draft_customer_email', label: 'Customer emails', proposals: 43, decided: 40,
  approved_as_is: 36, approved_with_edits: 3, rejected: 1, pending: 2, expired_unreviewed: 1,
  as_is_rate: 0.9, accepted_rate: 0.975, rejected_rate: 0.025, reliability_score: 0.7695,
  current_tier: 2, current_level: 'Draft for approval', recommended_tier: 3,
  recommended_level: 'Act within limits', assessment: 'ready',
  reason: 'Full autonomy needs 50+ reviewed proposals.', ...overrides,
});

const response: AutonomyReadiness = {
  action_types: [
    row({}),
    row({
      action_type: 'generate_external_report', label: 'External reports', proposals: 3, decided: 3,
      as_is_rate: null, rejected_rate: null, reliability_score: null, recommended_tier: null,
      recommended_level: null, assessment: 'insufficient_history', reason: 'Only 3 reviewed proposal(s).',
    }),
  ],
  rows_read: 46, rows_skipped: 1, total_decided: 43, insufficient_history: false, ready_count: 1,
  window_days: 365, truncated: false, min_decisions: 10, tiers: {}, settings_changed: false,
  status: 'analysis_only', source: 'connected_data', requires_human_review: true,
  actions_executed: false, generated_at: '2026-10-28T00:00:00Z',
};

describe('autonomy readiness page', () => {
  it('is limited to the roles that hold approve_actions', () => {
    expect([...meta.roles].sort()).toEqual(['manager', 'owner']);
    expect(meta.group).toBe('Governance');
  });

  it('renders reliability and the suggested level from the endpoint', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue(response);
    await render();
    expect(get).toHaveBeenCalledWith('/api/insights/autonomy-readiness');
    const text = container.textContent ?? '';
    expect(text).toContain('Customer emails');
    expect(text).toContain('90%');
    expect(text).toContain('Act within limits');
    expect(text).toContain('Could do more on its own');
    expect(text).toContain('External reports');
    expect(text).toContain('Not enough history yet');
    expect(text).toContain('never changes your approval settings');
    expect(container.querySelector('[role="note"]')?.textContent).toContain('connected business data');
  });

  it('says so when there is not enough history', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({ ...response, action_types: [], insufficient_history: true });
    await render();
    expect(container.textContent).toContain('Insufficient history');
  });

  it('shows an outage as an error, never as an empty result', async () => {
    vi.spyOn(api, 'get').mockRejectedValue(new Error('Approval history is unavailable; no analysis was produced'));
    await render();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Approval history is unavailable');
    expect(container.querySelector('table')).toBeNull();
    expect(container.textContent).not.toContain("hasn't proposed any actions");
  });
});
