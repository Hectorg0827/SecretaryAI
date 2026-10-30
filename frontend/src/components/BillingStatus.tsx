import { useCallback, useEffect, useId, useState, type ReactNode } from 'react';
import { AlertTriangle, ExternalLink, RefreshCw, ShieldCheck } from 'lucide-react';
import { api } from '../lib/api';
import { useAuth, type Role } from '../hooks/useAuth';

type SubscriptionRead = {
  status?: string;
  current_period_end?: string | null;
  cancel_at_period_end?: boolean;
  stripe_customer_id?: string | null;
  stripe_subscription_id?: string | null;
};

type PlanRead = { name?: string; slug?: string; id?: string };

type BillingRead = {
  subscription: SubscriptionRead | null;
  plan: PlanRead | null;
};

type PlanOption = {
  id: string;
  name?: string;
  price_monthly_usd?: number | string | null;
  price_yearly_usd?: number | string | null;
  max_users?: number | null;
  features?: unknown;
};

type BillingPeriod = 'monthly' | 'yearly';

export type BillingReturn = 'success' | 'cancelled' | 'portal';

// The server lets only the account owner start checkout or open the Stripe
// portal (manage_billing plus an explicit owner check, since view_all would
// otherwise admit managers). UI hint only; the server decides.
export const BILLING_MANAGER_ROLES: readonly Role[] = ['owner'];

// Stripe keeps billing these; a second checkout would start a second subscription.
const BILLED_STATUSES = ['active', 'trialing', 'past_due'];

const RETURN_NOTICES: Record<BillingReturn, string> = {
  success: 'You finished checkout on Stripe. Your subscription appears below once Stripe confirms the payment, '
    + 'which can take a minute. Use Refresh to check.',
  cancelled: 'You left Stripe checkout before paying, so no payment was made.',
  portal: 'You are back from Stripe. Any changes you made there appear below once Stripe confirms them. '
    + 'Use Refresh to check.',
};

/** Payment redirects may only go to Stripe's own HTTPS pages. */
export function isStripePageUrl(rawUrl: unknown, host: 'checkout.stripe.com' | 'billing.stripe.com'): rawUrl is string {
  if (typeof rawUrl !== 'string') return false;
  try {
    const parsed = new URL(rawUrl);
    return parsed.protocol === 'https:' && parsed.hostname === host && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}

function displayDate(value?: string | null): string {
  if (!value) return 'Not provided by billing service';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Date unavailable' : parsed.toLocaleDateString();
}

function displayPrice(value: number | string | null | undefined): string | null {
  const amount = typeof value === 'string' ? Number(value) : value;
  if (typeof amount !== 'number' || !Number.isFinite(amount) || amount < 0) return null;
  return amount.toLocaleString(undefined, { style: 'currency', currency: 'USD' });
}

function featureList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

/** The server's own plain-language reason, or a plain fallback when it never answered. */
function failureMessage(reason: unknown, fallback: string): string {
  if (reason instanceof TypeError) return 'SecretaryAI could not be reached. Check your internet connection and try again.';
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

function navigateTo(url: string) {
  window.location.assign(url);
}

const BUTTON_PRIMARY =
  'inline-flex items-center gap-1.5 bg-slate-800 text-white rounded-lg px-3 py-2 text-sm font-medium hover:bg-slate-700 disabled:opacity-50';
const BUTTON_SECONDARY =
  'inline-flex items-center gap-1.5 border border-slate-300 rounded-lg px-3 py-2 text-sm text-slate-800 bg-white hover:bg-slate-50 disabled:opacity-50';

function ErrorText({ children }: { children: ReactNode }) {
  return <p role="alert" className="text-sm text-red-800 bg-red-50 border border-red-200 rounded-lg p-3">{children}</p>;
}

function PlanPicker({ redirect }: { redirect: (url: string) => void }) {
  const [plans, setPlans] = useState<PlanOption[] | null>(null);
  const [plansError, setPlansError] = useState(false);
  const [period, setPeriod] = useState<BillingPeriod>('monthly');
  const [planId, setPlanId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const groupName = useId();

  const loadPlans = useCallback(async () => {
    setPlans(null);
    setPlansError(false);
    try {
      const result = await api.get<{ plans?: unknown }>('/api/billing/plans');
      if (!Array.isArray(result?.plans)) throw new Error('Plans unavailable');
      setPlans((result.plans as PlanOption[]).filter((plan) => typeof plan?.id === 'string' && plan.id.length > 0));
    } catch {
      setPlansError(true);
    }
  }, []);

  useEffect(() => { loadPlans(); }, [loadPlans]);

  async function startCheckout() {
    if (!planId) { setError('Choose a plan first.'); return; }
    setBusy(true);
    setError('');
    try {
      const result = await api.post<{ checkout_url?: unknown }>('/api/billing/checkout', {
        plan_id: planId, billing_period: period,
      });
      const url = result?.checkout_url;
      if (!isStripePageUrl(url, 'checkout.stripe.com')) {
        throw new Error('The payment service did not return a secure Stripe checkout page. Nothing was charged.');
      }
      redirect(url);
      // Stay busy: the browser is leaving for Stripe.
    } catch (reason) {
      setError(failureMessage(reason, 'Checkout could not be started. Nothing was charged.'));
      setBusy(false);
    }
  }

  const priceKey = period === 'yearly' ? 'price_yearly_usd' : 'price_monthly_usd';

  return (
    <section aria-label="Choose a plan" className="space-y-3 border-t border-slate-200 pt-4">
      <h2 className="text-sm font-semibold text-slate-800">Choose a plan</h2>
      {plansError ? (
        <div className="space-y-2">
          <ErrorText>Plans could not be loaded, so checkout is not available right now.</ErrorText>
          <button type="button" onClick={loadPlans} className={BUTTON_SECONDARY}>Try again</button>
        </div>
      ) : plans === null ? (
        <p role="status" className="text-sm text-slate-600">Loading plans…</p>
      ) : plans.length === 0 ? (
        <p role="status" className="text-sm text-slate-600">No plans are available to buy online right now.</p>
      ) : (
        <>
          <div role="radiogroup" aria-label="Billing period" className="inline-flex rounded-lg border border-slate-300 p-0.5 text-sm">
            {(['monthly', 'yearly'] as const).map((value) => (
              <button key={value} type="button" role="radio" aria-checked={period === value} disabled={busy}
                onClick={() => setPeriod(value)}
                className={`px-3 py-1.5 rounded-md ${period === value ? 'bg-slate-800 text-white' : 'text-slate-700'}`}>
                {value === 'monthly' ? 'Pay monthly' : 'Pay yearly'}
              </button>
            ))}
          </div>
          <fieldset className="space-y-2" disabled={busy}>
            <legend className="sr-only">Plan</legend>
            {plans.map((plan) => {
              const price = displayPrice(plan[priceKey]);
              const features = featureList(plan.features);
              return (
                <label key={plan.id}
                  className={`flex gap-3 rounded-lg border p-3 text-sm cursor-pointer ${planId === plan.id ? 'border-slate-800 bg-slate-50' : 'border-slate-200'}`}>
                  <input type="radio" name={groupName} value={plan.id} checked={planId === plan.id}
                    onChange={() => setPlanId(plan.id)} className="mt-1" />
                  <span className="space-y-1">
                    <span className="block font-medium text-slate-900">
                      {plan.name ?? plan.id}
                      {' · '}
                      {price ? `${price} per ${period === 'yearly' ? 'year' : 'month'}` : 'Price shown at checkout'}
                    </span>
                    {typeof plan.max_users === 'number' && (
                      <span className="block text-slate-600">Up to {plan.max_users} users</span>
                    )}
                    {features.length > 0 && <span className="block text-xs text-slate-500">{features.join(' · ')}</span>}
                  </span>
                </label>
              );
            })}
          </fieldset>
          <p className="text-xs text-slate-600">
            You will enter your card and confirm the payment on Stripe’s secure page. Nothing is charged until you
            finish there, and SecretaryAI never sees your card number.
          </p>
          <button type="button" onClick={startCheckout} disabled={busy} className={BUTTON_PRIMARY}>
            {busy ? 'Opening Stripe…' : <>Continue to secure payment <ExternalLink className="w-3 h-3" aria-hidden="true" /></>}
          </button>
          {error && <ErrorText>{error}</ErrorText>}
        </>
      )}
    </section>
  );
}

function ManageSubscription({ redirect }: { redirect: (url: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function openPortal() {
    setBusy(true);
    setError('');
    try {
      const result = await api.post<{ portal_url?: unknown }>('/api/billing/portal', {});
      const url = result?.portal_url;
      if (!isStripePageUrl(url, 'billing.stripe.com')) {
        throw new Error('The payment service did not return a secure Stripe page. Nothing was changed.');
      }
      redirect(url);
    } catch (reason) {
      setError(failureMessage(reason, 'The subscription page could not be opened. Nothing was changed.'));
      setBusy(false);
    }
  }

  return (
    <section aria-label="Manage subscription" className="space-y-2 border-t border-slate-200 pt-4">
      <p className="text-sm text-slate-700">
        Change your plan, update your card, download invoices or cancel on Stripe’s secure page.
      </p>
      <button type="button" onClick={openPortal} disabled={busy} className={BUTTON_PRIMARY}>
        {busy ? 'Opening Stripe…' : <>Manage subscription <ExternalLink className="w-3 h-3" aria-hidden="true" /></>}
      </button>
      {error && <ErrorText>{error}</ErrorText>}
    </section>
  );
}

/** Subscription record plus owner-only Stripe checkout and subscription management. */
export function BillingStatus({ returnStatus = null, redirect = navigateTo }: {
  returnStatus?: BillingReturn | null;
  /** Sends the browser to Stripe; replaceable in tests. */
  redirect?: (url: string) => void;
}) {
  const { role } = useAuth();
  const canManageBilling = BILLING_MANAGER_ROLES.includes(role);
  const [billing, setBilling] = useState<BillingRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.get<BillingRead>('/api/billing/subscription');
      setBilling(result);
      setError(false);
    } catch {
      setBilling(null);
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const subscription = billing?.subscription ?? null;
  const stillBilled = Boolean(subscription?.stripe_subscription_id)
    && BILLED_STATUSES.includes(subscription?.status ?? '');
  // Only offer purchase once the current record is known; an unknown status
  // could otherwise start a second, parallel subscription.
  const showPlans = canManageBilling && !loading && !error && !stillBilled;
  const showPortal = canManageBilling && !loading && !error && Boolean(subscription?.stripe_customer_id);

  return (
    <section aria-label="Billing status" className="space-y-4 py-4">
      <div className="rounded-lg bg-blue-50 border border-blue-200 p-4 text-sm text-blue-900">
        <ShieldCheck className="inline-block h-4 w-4 mr-1" aria-hidden="true" />
        Payments are taken by Stripe on its own secure page. SecretaryAI never sees your card number.
      </div>
      {returnStatus && RETURN_NOTICES[returnStatus] && (
        <p role="status" className="text-sm text-slate-800 bg-slate-50 border border-slate-200 rounded-lg p-3">
          {RETURN_NOTICES[returnStatus]}
        </p>
      )}
      <div className="flex justify-between items-center gap-3">
        <h2 className="text-sm font-semibold text-slate-800">Current subscription record</h2>
        <button type="button" onClick={refresh} disabled={loading}
          className="inline-flex items-center gap-1.5 px-3 py-2 rounded border border-slate-200 text-xs text-slate-700 disabled:opacity-50">
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>
      {loading ? (
        <p role="status" className="text-sm text-slate-600">Checking account billing data…</p>
      ) : error ? (
        <p role="alert" className="text-sm text-amber-900 bg-amber-50 border border-amber-200 rounded p-3">
          <AlertTriangle className="inline-block w-4 h-4 mr-1" /> Billing service unavailable. Subscription status is unknown,
          so plans and payment changes are not offered until it loads. Use Refresh to try again.
        </p>
      ) : !subscription ? (
        <p role="status" className="text-sm text-slate-600">
          No subscription record returned for this account. This does not confirm trial or payment eligibility.
        </p>
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-3 text-sm">
          <dt className="text-slate-500">Plan</dt>
          <dd className="font-medium text-slate-900">{billing?.plan?.name ?? billing?.plan?.slug ?? billing?.plan?.id ?? 'Plan not identified'}</dd>
          <dt className="text-slate-500">Recorded status</dt>
          <dd className="font-medium text-slate-900">{subscription.status ?? 'Unknown'}</dd>
          <dt className="text-slate-500">Current period ends</dt>
          <dd className="font-medium text-slate-900">{displayDate(subscription.current_period_end)}</dd>
          <dt className="text-slate-500">Cancellation scheduled</dt>
          <dd className="font-medium text-slate-900">
            {subscription.cancel_at_period_end === undefined ? 'Not reported' : subscription.cancel_at_period_end ? 'Yes' : 'No'}
          </dd>
        </dl>
      )}
      <p className="text-xs text-slate-500">
        Status above is SecretaryAI’s record. It changes only after Stripe confirms a payment or change.
      </p>
      {showPortal && <ManageSubscription redirect={redirect} />}
      {showPlans && <PlanPicker redirect={redirect} />}
      {!canManageBilling && (
        <p className="text-xs text-slate-500">Only the account owner can buy or change the subscription.</p>
      )}
    </section>
  );
}
