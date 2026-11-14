import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  getStatus: vi.fn(), getAlerts: vi.fn(), getDeadlines: vi.fn(),
  getLicenses: vi.fn(), getBrandRegistrations: vi.fn(),
  getFederalPermits: vi.fn(), getSetupStatus: vi.fn(),
}));
vi.mock('../lib/api', () => ({
  api: { compliance: mocks },
}));

import { Compliance } from './Compliance';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function flush() {
  for (let i = 0; i < 5; i += 1) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  }
}

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root?.render(<Compliance />); });
  await flush();
  return container;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.getSetupStatus.mockResolvedValue({ is_setup: true });
  mocks.getStatus.mockResolvedValue({
    overall: 'compliant', active_states: 1, total_products: 0,
    critical_alerts: 0, warning_alerts: 0, deadlines_due_7_days: 0,
    state_status: {},
  });
  mocks.getAlerts.mockResolvedValue([]);
  mocks.getDeadlines.mockResolvedValue([]);
  mocks.getLicenses.mockResolvedValue([]);
  mocks.getBrandRegistrations.mockResolvedValue([]);
  mocks.getFederalPermits.mockResolvedValue([]);
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
});

describe('compliance source failures', () => {
  it('does not mistake failed status, alerts, deadlines or license reads for zero records', async () => {
    mocks.getStatus.mockRejectedValue(new Error('synthetic 503'));
    mocks.getAlerts.mockRejectedValue(new Error('synthetic 503'));
    mocks.getDeadlines.mockRejectedValue(new Error('synthetic 503'));
    mocks.getLicenses.mockRejectedValue(new Error('synthetic 503'));
    const page = await render();
    const alert = page.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Compliance evidence is unavailable');
    expect(alert?.textContent).toContain('Licenses and permits');
    expect(alert?.textContent).toContain('Compliance status');
    expect(alert?.textContent).toContain('Compliance alerts');
    expect(alert?.textContent).toContain('Compliance deadlines');
    expect(page.textContent).toContain('No clearance decision can be made');
    expect(page.textContent).not.toContain('No compliance data yet');
  });

  it('shows setup-source failure instead of launching onboarding as if nothing exists', async () => {
    mocks.getSetupStatus.mockRejectedValue(new Error('synthetic 503'));
    const page = await render();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Compliance setup');
    expect(page.textContent).not.toContain('No compliance evidence');
  });

  it('lets the user retry failed reads and removes the warning after recovery', async () => {
    mocks.getStatus.mockRejectedValueOnce(new Error('synthetic 503'));
    const page = await render();
    expect(page.querySelector('[role="alert"]')).not.toBeNull();
    const retry = Array.from(page.querySelectorAll('button'))
      .find((button) => button.textContent?.trim() === 'Retry');
    await act(async () => { retry?.click(); });
    await flush();
    expect(mocks.getStatus).toHaveBeenCalledTimes(2);
    expect(page.querySelector('[role="alert"]')).toBeNull();
  });
});
