import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  pending: vi.fn(), approve: vi.fn(), reject: vi.fn(),
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
  auth: { canApprove: true },
}));

vi.mock('../../lib/api', () => ({
  api: { actions: { pending: mocks.pending, approve: mocks.approve, reject: mocks.reject } },
}));
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => mocks.auth }));
vi.mock('react-hot-toast', () => ({ default: mocks.toast }));

import { PendingApprovals } from './PendingApprovals';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

const draft = {
  id: 'synthetic-draft', action_type: 'draft_purchase_order', status: 'pending',
  created_at: '2026-10-26T12:00:00Z', content: { vendor_name: 'Fixture vendor', subtotal: 15 },
};

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root?.render(<PendingApprovals />); });
  await act(async () => { await Promise.resolve(); });
  return container;
}

function press(container: HTMLElement, label: string) {
  const button = [...container.querySelectorAll('button')].find((item) => item.textContent?.trim() === label);
  expect(button, `Expected ${label} control`).toBeTruthy();
  button?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  mocks.auth.canApprove = true;
  mocks.pending.mockResolvedValue({ drafts: [draft] });
  mocks.approve.mockResolvedValue({ draft_id: draft.id, status: 'approved', execution_status: 'queued' });
  mocks.reject.mockResolvedValue({});
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
  vi.unstubAllGlobals();
});

describe('human approval inbox widget (synthetic fixtures only)', () => {
  it('treats a malformed response as unknown, never as an empty approval queue', async () => {
    mocks.pending.mockResolvedValue({});
    const node = await render();
    expect(node.textContent).toContain('Approvals source unavailable. Pending status is unknown.');
    expect(node.textContent).not.toContain('Nothing waiting for approval');
  });

  it('shows the verified empty state only for an actual empty drafts array', async () => {
    mocks.pending.mockResolvedValue({ drafts: [] });
    const node = await render();
    expect(node.textContent).toContain('Nothing waiting for approval');
  });

  it('does not expose approve or reject controls to a viewer role', async () => {
    mocks.auth.canApprove = false;
    const node = await render();
    expect(node.textContent).toContain('Approval details are restricted to authorized reviewers.');
    expect(node.textContent).not.toContain('Fixture vendor');
    expect([...node.querySelectorAll('button')].some((b) => /Approve|Reject/.test(b.textContent ?? ''))).toBe(false);
    expect(mocks.pending).not.toHaveBeenCalled();
    expect(mocks.approve).not.toHaveBeenCalled();
  });

  it('never authorizes on cancel; approval queues action without claiming provider delivery', async () => {
    const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
    vi.stubGlobal('confirm', confirm);
    const node = await render();
    await act(async () => { press(node, 'Approve'); });
    expect(mocks.approve).not.toHaveBeenCalled();
    expect(node.textContent).toContain('Fixture vendor');

    await act(async () => { press(node, 'Approve'); });
    expect(mocks.approve).toHaveBeenCalledTimes(1);
    expect(mocks.approve).toHaveBeenCalledWith('synthetic-draft');
    expect(mocks.toast).toHaveBeenCalledWith('Approved; execution is queued, not confirmed complete.');
    expect(node.textContent).toContain('Nothing waiting for approval');
  });

  it('keeps the approval actionable when the explicit rejection request fails', async () => {
    mocks.reject.mockRejectedValue(new Error('Synthetic write failure'));
    const node = await render();
    await act(async () => { press(node, 'Reject'); });
    expect(mocks.toast.error).toHaveBeenCalledWith('Could not reject');
    expect(node.textContent).toContain('Fixture vendor');
  });
});
