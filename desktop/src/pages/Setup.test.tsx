// @vitest-environment happy-dom
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import Setup from './Setup';

const invokeMock = vi.fn();
const authFetchMock = vi.fn();
const openMock = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({ invoke: (...args: unknown[]) => invokeMock(...args) }));
vi.mock('@tauri-apps/plugin-shell', () => ({ open: (...args: unknown[]) => openMock(...args) }));
vi.mock('../auth', () => ({ authFetch: (...args: unknown[]) => authFetchMock(...args) }));

describe('desktop QB setup explicit consent', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    invokeMock.mockReset();
    invokeMock.mockImplementation(async (command: string) => command === 'check_qb_installed');
    authFetchMock.mockReset();
    openMock.mockReset();
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
  });

  it('detects installed QuickBooks without creating an authorization session', async () => {
    await act(async () => root.render(<Setup />));
    expect(host.textContent).toContain('Start authorization');
    expect(authFetchMock).not.toHaveBeenCalled();
    expect(openMock).not.toHaveBeenCalled();
  });

  it('requests a provider session only after a deliberate click', async () => {
    authFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ auth_flow_url: 'https://connect.conductor.is/example' }),
    });
    await act(async () => root.render(<Setup />));
    const button = [...host.querySelectorAll('button')].find((b) => b.textContent?.includes('Start authorization'));
    expect(button).toBeDefined();
    await act(async () => button!.click());
    expect(authFetchMock).toHaveBeenCalledWith('/api/setup/qb-desktop/start', { method: 'POST' });
    expect(host.textContent).toContain('Ready to authorize');
    expect(openMock).not.toHaveBeenCalled();
  });

  it('blocks unsafe provider links rather than opening them', async () => {
    authFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ auth_flow_url: 'http://localhost:5555/unsafe' }),
    });
    await act(async () => root.render(<Setup />));
    const button = [...host.querySelectorAll('button')].find((b) => b.textContent?.includes('Start authorization'));
    await act(async () => button!.click());
    expect(host.textContent).toContain('unsafe setup link');
    expect(openMock).not.toHaveBeenCalled();
  });
});
