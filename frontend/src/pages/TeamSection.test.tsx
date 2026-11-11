import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('../lib/api', () => ({ api: { get: mocks.get, post: mocks.post } }));

import { TeamSection } from './TeamSection';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.get.mockResolvedValue({ members: [] });
  mocks.post.mockResolvedValue({ status: 'sent', role: 'ar_specialist' });
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
});

async function renderTeam() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root?.render(<TeamSection />));
  return container;
}

describe('staging-only role invitations', () => {
  it('labels finance permissions and submits the explicitly selected role only', async () => {
    const node = await renderTeam();
    expect(node.textContent).toContain('read-only finance department specialist');
    const role = node.querySelector<HTMLSelectElement>('#team-role')!;
    const name = node.querySelector<HTMLInputElement>('#team-name')!;
    const email = node.querySelector<HTMLInputElement>('#team-email')!;
    const setInputValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    await act(async () => {
      role.value = 'ar_specialist';
      role.dispatchEvent(new Event('change', { bubbles: true }));
      setInputValue.call(name, 'Synthetic Finance Employee');
      name.dispatchEvent(new Event('input', { bubbles: true }));
      setInputValue.call(email, 'synthetic@example.com');
      email.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => {
      node.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(mocks.post).toHaveBeenCalledWith('/api/team/invitations', {
      full_name: 'Synthetic Finance Employee', email: 'synthetic@example.com', role: 'ar_specialist',
    });
    expect(node.textContent).toContain('activate their role');
    expect(node.textContent).not.toContain('Bearer');
  });
});
