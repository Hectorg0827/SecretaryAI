import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../../lib/api';
import GovernanceCheckPage, { meta, type GovernanceResult } from './governance-check';

let root: Root | undefined;
let container: HTMLDivElement;

async function render() {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><GovernanceCheckPage /></MemoryRouter>);
  });
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  vi.restoreAllMocks();
});

function setValue(el: HTMLInputElement | HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
}

function byLabel<T extends Element>(label: string) {
  return container.querySelector<T & Element>(`[aria-label="${label}"]`)!;
}

async function submit() {
  await act(async () => {
    container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

const result: GovernanceResult = {
  role: 'viewer', action: 'export', decision: 'blocked', highest_data_level: 'confidential',
  fields: [
    { field: 'customer_email', data_level: 'confidential', allowed: false, reason: "Role 'viewer' does not have permission to 'export' any data." },
    { field: 'sku', data_level: 'internal', allowed: false, reason: "Role 'viewer' does not have permission to 'export' any data." },
  ],
  blocked_fields: 2,
  anomalies: [{
    anomaly_type: 'bulk_export', severity: 'critical', would_block: true,
    description: 'This user performed 9 bulk exports in the last hour (data level: confidential)',
    recommended_action: 'Review exported data.',
  }],
  role_authority: { view: 'internal' },
  retention: [{ data_type: 'orders', retention_years: 7, basis: 'Tax compliance (IRS, state)', auto_purge: false, legal_hold_capable: true }],
  status: 'analysis_only', source: 'user_supplied', requires_human_review: true,
  actions_executed: false, generated_at: '2026-10-28T00:00:00Z',
};

describe('governance check page', () => {
  it('is limited to the roles that hold approve_actions', () => {
    expect([...meta.roles].sort()).toEqual(['manager', 'owner']);
  });

  it('submits the chosen role, action, data and activity', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue(result);
    await render();
    await act(async () => {
      setValue(byLabel<HTMLSelectElement>('Who'), 'viewer');
      setValue(byLabel<HTMLInputElement>('Other data (comma separated)'), 'sku, ar_balance');
      setValue(byLabel<HTMLInputElement>('Downloads in the last hour'), '9');
    });
    await act(async () => {
      [...container.querySelectorAll('label')].find((l) => l.textContent === 'Orders')!
        .querySelector('input')!.click();
    });
    await submit();
    expect(post).toHaveBeenCalledWith('/api/insights/governance-check/preview', {
      role: 'viewer', action: 'export',
      fields: ['customer_email', 'sku', 'ar_balance'],
      data_types: ['orders'],
      activity: { exports_last_hour: 9, access_hour: undefined },
    });
    const text = container.textContent ?? '';
    expect(text).toContain('Not allowed: 2 kind(s) of data');
    expect(text).toContain('customer_email');
    expect(text).toContain('This user performed 9 bulk exports');
    expect(text).toContain('7 years');
    expect(container.querySelector('[role="note"]')?.textContent).toContain('values you entered');
  });

  it('shows a failed check as an error', async () => {
    vi.spyOn(api, 'post').mockRejectedValue(new Error('Each data field may be listed once'));
    await render();
    await submit();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Each data field may be listed once');
    expect(container.querySelector('table')).toBeNull();
  });
});
