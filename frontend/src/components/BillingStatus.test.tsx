import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { BillingStatus, isStripePageUrl, type BillingReturn } from './BillingStatus';
import {
  button, cleanup, click, flush, mockApi, render, setRole, type RouteTable,
} from './logistics/logisticsTestHarness';
import type { Role } from '../hooks/useAuth';

const SUBSCRIPTION = '/api/billing/subscription';
const PLANS = '/api/billing/plans';
const CHECKOUT = '/api/billing/checkout';
const PORTAL = '/api/billing/portal';
const CHECKOUT_URL = 'https://checkout.stripe.com/c/pay/cs_test_synthetic';
const PORTAL_URL = 'https://billing.stripe.com/p/session/test_synthetic';

const PLAN_LIST = {
  plans: [
    { id: 'starter', name: 'Starter', price_monthly_usd: 99, price_yearly_usd: 990, max_users: 3, features: ['QB sync'] },
    { id: 'growth', name: 'Growth', price_monthly_usd: '249.00', price_yearly_usd: '2490.00', max_users: 10, features: [] },
  ],
};

const NO_SUBSCRIPTION = { subscription: null, plan: null };
const PAID = {
  subscription: {
    status: 'active', current_period_end: '2026-11-28T00:00:00Z', cancel_at_period_end: false,
    stripe_customer_id: 'cus_synthetic', stripe_subscription_id: 'sub_synthetic',
  },
  plan: { id: 'growth', name: 'Growth' },
};

async function show(role: Role, get: RouteTable, post: RouteTable = {}, returnStatus: BillingReturn | null = null) {
  setRole(role);
  const api = mockApi(get, post);
  const redirect = vi.fn();
  const container = await render(<BillingStatus redirect={redirect} returnStatus={returnStatus} />);
  return { ...api, container, redirect };
}

function radio(container: HTMLElement, label: string): HTMLElement {
  const match = Array.from(container.querySelectorAll('label')).find((item) => item.textContent?.includes(label));
  const input = match?.querySelector('input[type="radio"]');
  if (!input) throw new Error(`Plan option ${label} not found`);
  return input as HTMLElement;
}

function alertText(container: HTMLElement): string {
  return Array.from(container.querySelectorAll('[role="alert"]')).map((item) => item.textContent).join(' ');
}

afterEach(cleanup);

describe('billing checkout', () => {
  it('starts checkout for the chosen plan and sends the browser to Stripe', async () => {
    const { container, post, redirect } = await show('owner',
      { [SUBSCRIPTION]: NO_SUBSCRIPTION, [PLANS]: PLAN_LIST },
      { [CHECKOUT]: { checkout_url: CHECKOUT_URL, session_id: 'cs_test_synthetic' } });
    expect(container.textContent).toContain('Starter');
    expect(container.textContent).toContain('$99.00 per month');
    await click(button(container, 'Pay yearly'));
    expect(container.textContent).toContain('$2,490.00 per year');
    await click(radio(container, 'Growth'));
    await click(button(container, 'Continue to secure payment'));
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(CHECKOUT, { plan_id: 'growth', billing_period: 'yearly' });
    expect(redirect).toHaveBeenCalledWith(CHECKOUT_URL);
    expect(container.textContent).toContain('Opening Stripe');
  });

  it('asks for a plan before contacting the server', async () => {
    const { container, post, redirect } = await show('owner', { [SUBSCRIPTION]: NO_SUBSCRIPTION, [PLANS]: PLAN_LIST });
    await click(button(container, 'Continue to secure payment'));
    expect(alertText(container)).toContain('Choose a plan first.');
    expect(post).not.toHaveBeenCalled();
    expect(redirect).not.toHaveBeenCalled();
  });

  it('shows the server refusal in plain language and does not redirect', async () => {
    const refusal = 'This account already has a subscription. Use Manage subscription to change the plan or payment details.';
    const { container, redirect } = await show('owner', { [SUBSCRIPTION]: NO_SUBSCRIPTION, [PLANS]: PLAN_LIST },
      { [CHECKOUT]: new Error(refusal) });
    await click(radio(container, 'Starter'));
    await click(button(container, 'Continue to secure payment'));
    expect(alertText(container)).toContain(refusal);
    expect(redirect).not.toHaveBeenCalled();
    expect(button(container, 'Continue to secure payment')?.disabled).toBe(false);
  });

  it('explains a lost connection instead of showing a technical error', async () => {
    const { container, redirect } = await show('owner', { [SUBSCRIPTION]: NO_SUBSCRIPTION, [PLANS]: PLAN_LIST },
      { [CHECKOUT]: new TypeError('Failed to fetch') });
    await click(radio(container, 'Starter'));
    await click(button(container, 'Continue to secure payment'));
    expect(alertText(container)).toContain('SecretaryAI could not be reached');
    expect(alertText(container)).not.toContain('Failed to fetch');
    expect(redirect).not.toHaveBeenCalled();
  });

  it.each([
    'https://mock-stripe.example.com/checkout?plan=starter',
    'http://checkout.stripe.com/c/pay/cs_test',
    'https://checkout.stripe.com.attacker.invalid/pay',
    '',
    undefined,
  ])('refuses to send the browser to a non-Stripe checkout address (%s)', async (url) => {
    const { container, redirect } = await show('owner', { [SUBSCRIPTION]: NO_SUBSCRIPTION, [PLANS]: PLAN_LIST },
      { [CHECKOUT]: { checkout_url: url } });
    await click(radio(container, 'Starter'));
    await click(button(container, 'Continue to secure payment'));
    expect(alertText(container)).toContain('did not return a secure Stripe checkout page. Nothing was charged.');
    expect(redirect).not.toHaveBeenCalled();
  });

  it('does not offer checkout when plans cannot be loaded', async () => {
    const { container } = await show('owner', { [SUBSCRIPTION]: NO_SUBSCRIPTION, [PLANS]: new Error('unavailable') });
    expect(alertText(container)).toContain('Plans could not be loaded');
    expect(button(container, 'Continue to secure payment')).toBeUndefined();
  });

  it('does not offer checkout or management while the subscription status is unknown', async () => {
    const { container, get } = await show('owner', { [SUBSCRIPTION]: new Error('Billing unavailable'), [PLANS]: PLAN_LIST });
    expect(alertText(container)).toContain('Billing service unavailable');
    expect(button(container, 'Continue to secure payment')).toBeUndefined();
    expect(button(container, 'Manage subscription')).toBeUndefined();
    expect(get).not.toHaveBeenCalledWith(PLANS);
  });
});

describe('billing management', () => {
  it('opens the Stripe portal for a paying owner and hides a second checkout', async () => {
    const { container, get, post, redirect } = await show('owner', { [SUBSCRIPTION]: PAID },
      { [PORTAL]: { portal_url: PORTAL_URL } });
    expect(container.textContent).toContain('Growth');
    expect(button(container, 'Continue to secure payment')).toBeUndefined();
    expect(get).not.toHaveBeenCalledWith(PLANS);
    await click(button(container, 'Manage subscription'));
    expect(post).toHaveBeenCalledWith(PORTAL, {});
    expect(redirect).toHaveBeenCalledWith(PORTAL_URL);
  });

  it('shows a portal failure without redirecting', async () => {
    const message = 'The payment service could not open the subscription page. Nothing was changed. Please try again in a few minutes.';
    const { container, redirect } = await show('owner', { [SUBSCRIPTION]: PAID }, { [PORTAL]: new Error(message) });
    await click(button(container, 'Manage subscription'));
    expect(alertText(container)).toContain(message);
    expect(redirect).not.toHaveBeenCalled();
    expect(button(container, 'Manage subscription')?.disabled).toBe(false);
  });

  it('refuses a portal address that is not Stripe', async () => {
    const { container, redirect } = await show('owner', { [SUBSCRIPTION]: PAID },
      { [PORTAL]: { portal_url: 'https://mock-stripe.example.com/portal' } });
    await click(button(container, 'Manage subscription'));
    expect(alertText(container)).toContain('did not return a secure Stripe page');
    expect(redirect).not.toHaveBeenCalled();
  });

  it('offers both a new plan and the portal after a cancelled subscription', async () => {
    const cancelled = { ...PAID, subscription: { ...PAID.subscription, status: 'canceled' } };
    const { container } = await show('owner', { [SUBSCRIPTION]: cancelled, [PLANS]: PLAN_LIST });
    expect(button(container, 'Manage subscription')).toBeDefined();
    expect(button(container, 'Continue to secure payment')).toBeDefined();
  });

  it.each(['manager', 'back_office', 'sales_rep', 'viewer'] as Role[])(
    'hides purchase and management from %s', async (role) => {
      const { container, get, post } = await show(role, { [SUBSCRIPTION]: { ...PAID, subscription: {
        ...PAID.subscription, status: 'canceled' } }, [PLANS]: PLAN_LIST });
      await flush();
      expect(button(container, 'Manage subscription')).toBeUndefined();
      expect(button(container, 'Continue to secure payment')).toBeUndefined();
      expect(get).not.toHaveBeenCalledWith(PLANS);
      expect(post).not.toHaveBeenCalled();
      expect(container.textContent).toContain('Only the account owner can buy or change the subscription.');
    });
});

describe('returning from Stripe', () => {
  it('does not claim a payment succeeded before Stripe confirms it', async () => {
    const { container } = await show('owner', { [SUBSCRIPTION]: NO_SUBSCRIPTION, [PLANS]: PLAN_LIST }, {}, 'success');
    expect(container.textContent).toContain('Your subscription appears below once Stripe confirms the payment');
    expect(container.textContent).toContain('No subscription record returned');
  });

  it('confirms nothing was paid after a cancelled checkout', async () => {
    const { container } = await show('owner', { [SUBSCRIPTION]: NO_SUBSCRIPTION, [PLANS]: PLAN_LIST }, {}, 'cancelled');
    expect(container.textContent).toContain('no payment was made');
  });
});

describe('isStripePageUrl', () => {
  it('accepts only HTTPS Stripe hosts without credentials', () => {
    expect(isStripePageUrl(CHECKOUT_URL, 'checkout.stripe.com')).toBe(true);
    expect(isStripePageUrl(PORTAL_URL, 'billing.stripe.com')).toBe(true);
    expect(isStripePageUrl(PORTAL_URL, 'checkout.stripe.com')).toBe(false);
    expect(isStripePageUrl('https://user:pass@checkout.stripe.com/x', 'checkout.stripe.com')).toBe(false);
    expect(isStripePageUrl('javascript:alert(1)', 'checkout.stripe.com')).toBe(false);
    expect(isStripePageUrl('/settings', 'checkout.stripe.com')).toBe(false);
    expect(isStripePageUrl(42, 'checkout.stripe.com')).toBe(false);
  });
});
