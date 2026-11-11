import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() }));
vi.mock('../lib/api', () => ({ api: {
  get: mocks.get, post: mocks.post, patch: mocks.patch, delete: mocks.delete,
} }));

import { TeamSection } from './TeamSection';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.get.mockResolvedValue({ members: [] });
  mocks.post.mockResolvedValue({ status: 'sent', role: 'ar_specialist' });
  mocks.patch.mockResolvedValue({ status: 'updated' });
  mocks.delete.mockResolvedValue({ status: 'deactivated' });
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

  it('changes staff roles and requires a second step before disabling access', async () => {
    const staffId = 'd60b1f3e-76b8-4132-bd68-be88151c3554';
    const member = { id: staffId, full_name: 'Synthetic Manager', email: 'manager@example.com', role: 'manager', is_active: true };
    mocks.get.mockResolvedValueOnce({ members: [
      { id: 'owner-id', full_name: 'Owner', email: 'owner@example.com', role: 'owner', is_active: true },
      member,
    ] }).mockResolvedValueOnce({ members: [{ ...member, role: 'ar_specialist' }] })
      .mockResolvedValueOnce({ members: [{ ...member, role: 'ar_specialist', is_active: false }] });
    const node = await renderTeam();
    expect(node.textContent).toContain('protected');
    expect(node.querySelectorAll('select[id^="member-role-"]')).toHaveLength(1);
    const select = node.querySelector<HTMLSelectElement>(`#member-role-${staffId}`)!;
    await act(async () => {
      select.value = 'ar_specialist';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => {
      Array.from(node.querySelectorAll('button')).find((b) => b.textContent === 'Save role')!.click();
    });
    expect(mocks.patch).toHaveBeenCalledWith(`/api/team/members/${staffId}/role`, { role: 'ar_specialist' });
    expect(node.textContent).toContain('old session can no longer access');

    await act(async () => {
      Array.from(node.querySelectorAll('button')).find((b) => b.textContent === 'Disable')!.click();
    });
    expect(mocks.delete).not.toHaveBeenCalled();
    await act(async () => {
      Array.from(node.querySelectorAll('button')).find((b) => b.textContent === 'Confirm disable')!.click();
    });
    expect(mocks.delete).toHaveBeenCalledWith(`/api/team/members/${staffId}`);
    expect(node.textContent).toContain('disabled');
    expect(node.querySelectorAll('select[id^="member-role-"]')).toHaveLength(0);
  });
});
