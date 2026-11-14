import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  getStatus: vi.fn(), getAlerts: vi.fn(), getDeadlines: vi.fn(),
  getLicenses: vi.fn(), getBrandRegistrations: vi.fn(),
  getFederalPermits: vi.fn(), getSetupStatus: vi.fn(),
}));
vi.mock('../lib/api', () => ({ api: { compliance: mocks } }));
// Only test the source-integrity status, not the wizard's unrelated form fields.
vi.mock('../components/compliance/ComplianceSetupWizard', () => ({
  ComplianceSetupWizard: () => null,
}));

import { Compliance } from './Compliance';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root?.render(<Compliance />); });
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  return container;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.getSetupStatus.mockResolvedValue({ is_setup: true });
  mocks.getStatus.mockResolvedValue({
    overall: 'ok', active_states: 0, total_products: 0,
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

describe('compliance clearance evidence', () => {
  it('never calls an empty but configured registry legally compliant', async () => {
    const page = await render();
    expect(page.textContent).toContain('No recorded alerts');
    expect(page.textContent).toContain('not independent verification by regulators');
    expect(page.textContent).not.toContain('Compliant');
  });

  it('does not show a green clearance when setup is incomplete', async () => {
    mocks.getSetupStatus.mockResolvedValue({
      is_setup: false, missing: ['federal_permits', 'products', 'licenses'],
    });
    const page = await render();
    expect(page.textContent).toContain('Setup incomplete · clearance not verified');
    expect(page.textContent).toContain('No recorded alerts does not mean regulatory approval');
    expect(page.textContent).not.toContain('Compliant');
    expect(page.textContent).not.toContain('No recorded alerts' + '</span>');
  });

  it('does not claim clearance if setup source fails even when status says ok', async () => {
    mocks.getSetupStatus.mockRejectedValue(new Error('synthetic provider outage'));
    const page = await render();
    expect(page.textContent).toContain('Compliance evidence unavailable');
    expect(page.textContent).not.toContain('No recorded alerts');
    expect(page.textContent).not.toContain('Compliant');
  });

  it('treats a malformed setup response as unavailable', async () => {
    mocks.getSetupStatus.mockResolvedValue({});
    const page = await render();
    expect(page.textContent).toContain('Compliance evidence unavailable');
    expect(page.textContent).not.toContain('No recorded alerts');
  });

  it('suppresses positive status if alert or permit evidence failed to load', async () => {
    mocks.getLicenses.mockRejectedValue(new Error('synthetic registry outage'));
    const page = await render();
    expect(page.textContent).toContain('Compliance evidence unavailable');
    expect(page.textContent).not.toContain('No recorded alerts');
  });
});
