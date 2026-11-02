import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../../lib/api';
import OnboardingPlanPage, { meta, type OnboardingPlan } from './onboarding-plan';

let root: Root | undefined;
let container: HTMLDivElement;

async function render() {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><OnboardingPlanPage /></MemoryRouter>);
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

const field = <T extends Element>(label: string) =>
  container.querySelector<T & Element>(`[aria-label="${label}"]`)!;

async function submit() {
  await act(async () => {
    container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

const plan: OnboardingPlan = {
  plan: {
    role_title: 'Driver', department: 'delivery', start_date: '2026-11-02', as_of: '2026-10-28',
    due_soon_days: 7,
    items: [
      { item_id: 'paperwork', title: 'Send the offer letter and hiring paperwork', kind: 'onboarding', owner: 'Office',
        custom: false, days_from_start: -3, due_on: '2026-10-30', status: 'due_soon', completed_on: null },
      { item_id: 'driver-license', title: 'Verify the driving licence', kind: 'license', owner: 'Office',
        custom: false, days_from_start: 0, due_on: '2026-11-02', status: 'due_soon', completed_on: null },
      { item_id: 'custom-1', title: 'Order uniform', kind: 'onboarding', owner: 'Manager',
        custom: true, days_from_start: 30, due_on: '2026-12-02', status: 'upcoming', completed_on: null },
    ],
    next_steps: ['Send the offer letter and hiring paperwork'],
  },
  summary: { total: 3, overdue: 0, due_soon: 2, completed: 0, upcoming: 1 },
  status: 'analysis_only', source: 'user_supplied', requires_human_review: true,
  actions_executed: false, generated_at: '2026-10-28T00:00:00Z',
};

describe('onboarding plan page', () => {
  it('matches the roles the backend admits for manage_users', () => {
    expect([...meta.roles].sort()).toEqual(['manager', 'owner']);
    expect(meta.group).toBe('People');
  });

  it('submits the form and renders the dated plan', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue(plan);
    await render();
    await act(async () => {
      setValue(field<HTMLInputElement>('Job title'), 'Driver');
      setValue(field<HTMLSelectElement>('Team'), 'delivery');
      setValue(field<HTMLInputElement>('Start date'), '2026-11-02');
    });
    await act(async () => {
      [...container.querySelectorAll('button')].find((b) => b.textContent?.includes('Add a task'))!.click();
    });
    await act(async () => {
      setValue(field<HTMLInputElement>('Extra task 1'), 'Order uniform');
      setValue(field<HTMLInputElement>('Days from start for task 1'), '30');
    });
    await submit();
    expect(post).toHaveBeenCalledWith('/api/insights/onboarding-plan/preview', {
      role_title: 'Driver', department: 'delivery', start_date: '2026-11-02', include_standard: true,
      custom_tasks: [{ title: 'Order uniform', days_from_start: 30 }], completed: [],
    });
    const text = container.textContent ?? '';
    expect(text).toContain('Verify the driving licence');
    expect(text).toContain('Order uniform');
    expect(text).toContain('Due soon');
    expect(text).toContain('Do next');
    expect(container.querySelector('[role="note"]')?.textContent).toContain('values you entered');
  });

  it('re-runs the plan with a task marked done', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue(plan);
    await render();
    await submit();
    await act(async () => {
      field<HTMLInputElement>('Mark done: Verify the driving licence').click();
    });
    expect(post).toHaveBeenCalledTimes(2);
    const body = post.mock.calls[1][1] as { completed: { item_id: string; completed_on: string }[] };
    expect(body.completed).toEqual([
      { item_id: 'driver-license', completed_on: new Date().toISOString().slice(0, 10) },
    ]);
  });

  it('shows a rejected plan as an error', async () => {
    vi.spyOn(api, 'post').mockRejectedValue(new Error('Start date must be within a year of today'));
    await render();
    await submit();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Start date must be within a year');
    expect(container.querySelector('table')).toBeNull();
  });
});
