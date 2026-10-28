import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ summary: vi.fn() }));

vi.mock('../lib/api', () => ({ api: { dashboard: { summary: mocks.summary } } }));
vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    role: 'owner', hasPermission: (permission: string) => permission === 'trigger_actions',
    canApprove: false, canAccessChat: false,
  }),
}));
vi.mock('../components/layout/TopBar', () => ({ TopBar: () => null }));
vi.mock('../components/dashboard/EmailInbox', () => ({ EmailInbox: () => null }));
vi.mock('../components/dashboard/ProactiveFeed', () => ({ ProactiveFeed: () => null }));
vi.mock('../components/dashboard/MorningBriefing', () => ({ MorningBriefing: () => null }));
vi.mock('../components/dashboard/StatStrip', () => ({
  StatStrip: ({ stats }: { stats: Array<{ label: string; value: string | number }> }) => (
    <div>{stats.map((item) => <span key={item.label}>{item.label}: {item.value}</span>)}</div>
  ),
}));

import { Dashboard } from './Dashboard';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

const validSummary = (unreadEmails: unknown = 3) => ({
  accounts: { healthy: 1, slowing: 0, at_risk: 0, dormant: 0 },
  inventory_alerts: [], pending_actions: 0, unread_emails: unreadEmails,
  generated_at: '2026-10-26T12:00:00Z',
});

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root?.render(<Dashboard />); });
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

describe('dashboard summary source and reload sequence (synthetic only)', () => {
  it('shows unavailable instead of zero when the email count is absent', async () => {
    mocks.summary.mockResolvedValue(validSummary(null));
    const node = await render();
    expect(node.textContent).toContain('Unread Emails: Unavailable');
    expect(node.textContent).not.toContain('Unread Emails: 0');
  });

  it('uses a read-only summary GET when manually reloading', async () => {
    mocks.summary.mockResolvedValue(validSummary());
    const node = await render();
    const reload = [...node.querySelectorAll('button')].find((button) => button.textContent === 'Reload summary');
    expect(reload).toBeTruthy();
    await act(async () => { reload?.click(); });
    expect(mocks.summary).toHaveBeenCalledTimes(2);
    expect(node.textContent).toContain('Unread Emails: 3');
  });

  it('keeps a newer refresh result when an older response arrives last', async () => {
    let resolveOld!: (value: unknown) => void;
    let resolveNew!: (value: unknown) => void;
    mocks.summary
      .mockResolvedValueOnce(validSummary(2))
      .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }))
      .mockReturnValueOnce(new Promise((resolve) => { resolveNew = resolve; }));
    const node = await render();
    const reload = [...node.querySelectorAll('button')].find((button) => button.textContent === 'Reload summary');
    expect(reload).toBeTruthy();

    await act(async () => {
      reload?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      reload?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(mocks.summary).toHaveBeenCalledTimes(3);
    await act(async () => { resolveNew(validSummary(7)); });
    expect(node.textContent).toContain('Unread Emails: 7');
    await act(async () => { resolveOld(validSummary(1)); });
    expect(node.textContent).toContain('Unread Emails: 7');
    expect(node.textContent).not.toContain('Unread Emails: 1');
  });
});
