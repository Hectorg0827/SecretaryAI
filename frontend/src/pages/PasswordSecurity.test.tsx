import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock('../lib/api', () => ({ api: { post: mocks.post } }));

import { ChangePassword, ForgotPassword, ResetPassword } from './PasswordSecurity';

let root: Root | undefined;
let host: HTMLDivElement | undefined;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  localStorage.clear();
  window.history.replaceState({}, '', '/login');
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
});

async function render(component: ReactNode) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => root?.render(<MemoryRouter>{component}</MemoryRouter>));
  return host;
}

async function typeValue(selector: string, value: string) {
  const field = host!.querySelector<HTMLInputElement>(selector)!;
  const nativeSet = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  await act(async () => {
    nativeSet.call(field, value);
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function submit() {
  await act(async () => {
    host!.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

describe('staging identity screens', () => {
  it('requests a reset without revealing an account', async () => {
    mocks.post.mockResolvedValue({ status: 'ok' });
    const node = await render(<ForgotPassword />);
    await typeValue('#recovery-email', 'Pilot@Example.com');
    await submit();
    expect(mocks.post).toHaveBeenCalledWith('/auth/forgot-password', { email: 'pilot@example.com' });
    expect(node.textContent).toContain('If that email has an account');
  });

  it('uses the fragment reset token once and clears it from the address', async () => {
    const token = 'a'.repeat(44);
    window.history.replaceState({}, '', `/reset-password#token=${token}`);
    mocks.post.mockResolvedValue({ status: 'ok' });
    const node = await render(<ResetPassword />);
    expect(window.location.hash).toBe('');
    await typeValue('#reset-password', 'FreshPassword987');
    await typeValue('#reset-confirm', 'FreshPassword987');
    await submit();
    expect(mocks.post).toHaveBeenCalledWith('/auth/reset-password', {
      token, new_password: 'FreshPassword987',
    });
    expect(node.textContent).toContain('Please sign in again');
  });

  it('stores only the new signed session after a successful password change', async () => {
    mocks.post.mockResolvedValue({ status: 'ok', access_token: 'new-opaque-session' });
    const node = await render(<ChangePassword />);
    await typeValue('#current-password', 'OriginalPassword123');
    await typeValue('#change-password', 'FreshPassword987');
    await typeValue('#change-confirm', 'FreshPassword987');
    await submit();
    expect(mocks.post).toHaveBeenCalledWith('/auth/change-password', {
      current_password: 'OriginalPassword123', new_password: 'FreshPassword987',
    });
    expect(localStorage.getItem('secretary_token')).toBe('new-opaque-session');
    expect(node.textContent).toContain('Other devices must sign in again');
  });
});
