import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  summary: vi.fn(),
  sendMessage: vi.fn(),
  role: 'owner',
}));

vi.mock('../lib/api', () => ({
  api: { get: mocks.get, dashboard: { summary: mocks.summary } },
}));
vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    role: mocks.role,
    hasPermission: (permission: string) => mocks.role === 'owner'
      || (mocks.role === 'viewer' && permission === 'view_dashboard'),
    canAccessChat: mocks.role === 'owner',
    canApprove: mocks.role === 'owner',
  }),
}));
vi.mock('../stores/chatStore', () => ({
  useChatStore: (selector: (state: { isStreaming: boolean; sendMessage: (text: string) => Promise<void> }) => unknown) =>
    selector({ isStreaming: false, sendMessage: mocks.sendMessage }),
}));
vi.mock('../components/chat/ChatInterface', () => ({
  ChatInterface: () => <div>Conversation connected</div>,
}));

import { LolaHome } from './LolaHome';

let root: Root | null = null;
let container: HTMLDivElement | null = null;

async function renderHome() {
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  await act(async () => { root?.render(<MemoryRouter><LolaHome /></MemoryRouter>); });
  return container;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  vi.clearAllMocks();
  mocks.role = 'owner';
  mocks.get.mockResolvedValue({ id: 'user-alpha', full_name: 'Synthetic User' });
  mocks.summary.mockResolvedValue({
    accounts: null, inventory_alerts: [], inventory_unassessed: null,
    pending_actions: 3, unread_emails: null,
    generated_at: '2026-11-11T15:00:00Z', snapshot_stale: true, cached: true,
  });
  mocks.sendMessage.mockResolvedValue(undefined);
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  container?.remove();
  container = null;
});

describe('Lola-first homepage (synthetic data only)', () => {
  it('opens with an off-by-default voice greeting and a central Lola assistant', async () => {
    const node = await renderHome();
    expect(node.querySelector('[role="img"]')?.getAttribute('aria-label')).toContain('Lola');
    expect(node.querySelector('[aria-label="Automatic Lola voice greeting off"]')?.getAttribute('aria-pressed')).toBe('false');
    expect(localStorage.getItem('lionbi:lola:voice-greeting:user-alpha')).toBeNull();
    expect(node.textContent).toContain("I'm Lola");
  });

  it('persists opt-in only under the current authenticated user id', async () => {
    const node = await renderHome();
    const toggle = node.querySelector<HTMLButtonElement>('[aria-label="Automatic Lola voice greeting off"]');
    expect(toggle).not.toBeNull();
    await act(async () => { toggle?.click(); });
    expect(localStorage.getItem('lionbi:lola:voice-greeting:user-alpha')).toBe('on');
    expect(node.querySelector('[aria-label="Automatic Lola voice greeting on"]')).not.toBeNull();
    await act(async () => { node.querySelector<HTMLButtonElement>('[aria-label="Automatic Lola voice greeting on"]')?.click(); });
    expect(localStorage.getItem('lionbi:lola:voice-greeting:user-alpha')).toBe('off');
  });

  it('hands a suggested prompt to the existing authenticated chat and opens the conversation', async () => {
    const node = await renderHome();
    const prompt = [...node.querySelectorAll('button')].find((item) => item.textContent?.includes('What needs my attention?'));
    await act(async () => { prompt?.click(); });
    expect(mocks.sendMessage).toHaveBeenCalledWith('What needs my attention?');
    expect(node.textContent).toContain('Conversation connected');
  });

  it('distinguishes unavailable business evidence from zero results', async () => {
    mocks.summary.mockRejectedValue(new Error('offline'));
    const node = await renderHome();
    expect(node.textContent).toContain('Business snapshot unavailable');
    expect(node.textContent).toContain('Not verified');
    expect(node.textContent).not.toContain('Inventory alerts 0');
  });

  it('does not offer chat or restricted workspaces to a view-only role', async () => {
    mocks.role = 'viewer';
    const node = await renderHome();
    expect(node.textContent).toContain('not yet enabled for this role');
    expect(node.querySelector('[aria-label="Send to Lola"]')).toBeNull();
    expect(node.textContent).not.toContain('Sales & accounts');
  });
});
