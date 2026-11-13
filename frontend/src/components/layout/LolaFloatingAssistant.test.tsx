// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useLolaPanelStore } from '../../stores/lolaPanelStore';
import { useChatStore } from '../../stores/chatStore';

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ canAccessChat: true }),
}));
vi.mock('../chat/ChatInterface', () => ({
  ChatInterface: ({ context }: { context?: { workspace: string } }) =>
    <div>Shared Lola conversation {context?.workspace}</div>,
}));

import { LolaFloatingAssistant } from './LolaFloatingAssistant';

let root: Root | null = null;
let node: HTMLDivElement | null = null;

async function renderAt(path: string) {
  node = document.createElement('div');
  document.body.appendChild(node);
  root = createRoot(node);
  await act(async () => {
    root?.render(<MemoryRouter initialEntries={[path]}><LolaFloatingAssistant /></MemoryRouter>);
  });
  return node;
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  node?.remove();
  node = null;
  useLolaPanelStore.setState({open:false,context:null});
  useChatStore.setState({messages:[],conversationId:null,context:null,isStreaming:false});
});

describe('one floating Lola assistant', () => {
  it('offers a contextual launcher in the Inventory workspace', async () => {
    const ui = await renderAt('/inventory');
    const trigger = ui.querySelector<HTMLButtonElement>('button[aria-label="Ask Lola about this page"]');
    expect(trigger).not.toBeNull();
    await act(async () => { trigger?.click(); });
    expect(ui.querySelector('[role="dialog"][aria-label="Lola contextual assistant"]')).not.toBeNull();
    expect(ui.textContent).toContain('Shared Lola conversation inventory');
    expect(useLolaPanelStore.getState().context?.workspace).toBe('inventory');
  });

  it('never adds a second floating chat on Home', async () => {
    const ui = await renderAt('/');
    expect(ui.querySelector('.lola-float__launcher')).toBeNull();
    expect(ui.querySelector('[role="dialog"]')).toBeNull();
  });
});
