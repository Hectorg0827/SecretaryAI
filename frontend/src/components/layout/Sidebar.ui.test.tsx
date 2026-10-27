import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  status: vi.fn(), get: vi.fn(),
}));
vi.mock('../../lib/api', () => ({
  api: { agent: { status: mocks.status }, get: mocks.get },
}));
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ role: 'owner' }) }));

import { useInboxStore } from '../../stores/inboxStore';
import { Sidebar } from './Sidebar';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function renderSidebar() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><Sidebar /></MemoryRouter>);
  });
  return container;
}

function inboxBadge() {
  return container?.querySelector('[aria-label$="unread items in the last loaded Inbox page"]');
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.status.mockResolvedValue({ connected: false });
  mocks.get.mockResolvedValue({ name: 'Synthetic owner' });
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
    matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }));
  useInboxStore.setState({ unread_count: null, filter: 'all', items: [] });
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
  vi.unstubAllGlobals();
});

describe('sidebar badge source integrity (synthetic only)', () => {
  it('does not claim a count before Inbox loads or for a verified zero', async () => {
    await renderSidebar();
    expect(inboxBadge()).toBeNull();
    await act(async () => { useInboxStore.setState({ unread_count: 0 }); });
    expect(inboxBadge()).toBeNull();
  });

  it('shows the unfiltered loaded-page count but hides it for filtered and unknown reads', async () => {
    await renderSidebar();
    await act(async () => { useInboxStore.setState({ unread_count: 4 }); });
    expect(inboxBadge()?.getAttribute('aria-label')).toContain('4 unread items');
    await act(async () => { useInboxStore.setState({ filter: 'email', unread_count: 2 }); });
    expect(inboxBadge()).toBeNull();
    await act(async () => { useInboxStore.setState({ filter: 'all', unread_count: null }); });
    expect(inboxBadge()).toBeNull();
  });
});
