import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { Settings } from './Settings';
import { cleanup, mockApi, render, setRole } from '../components/logistics/logisticsTestHarness';

const ROUTES = {
  '/api/settings/integrations': new Error('not needed for this test'),
  '/api/billing/subscription': { subscription: null, plan: null },
  '/api/billing/plans': { plans: [] },
};

afterEach(async () => {
  await cleanup();
  window.history.replaceState({}, '', '/');
});

describe('Settings after returning from Stripe', () => {
  it('opens Billing for the owner, explains the return and clears the marker', async () => {
    window.history.replaceState({}, '', '/settings?billing=cancelled');
    setRole('owner');
    mockApi(ROUTES);
    const container = await render(<Settings />);
    expect(container.querySelector('[aria-label="Billing status"]')).not.toBeNull();
    expect(container.textContent).toContain('no payment was made');
    expect(window.location.search).toBe('');
  });

  it('ignores the marker for roles without billing', async () => {
    window.history.replaceState({}, '', '/settings?billing=success');
    setRole('manager');
    mockApi(ROUTES);
    const container = await render(<Settings />);
    expect(container.querySelector('[aria-label="Billing status"]')).toBeNull();
    expect(container.textContent).not.toContain('Stripe confirms the payment');
  });
});
