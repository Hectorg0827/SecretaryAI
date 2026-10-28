import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, RefreshCw, ShieldCheck } from 'lucide-react';
import { api } from '../lib/api';

type SubscriptionRead = {
  status?: string;
  current_period_end?: string | null;
  cancel_at_period_end?: boolean;
};

type PlanRead = { name?: string; slug?: string; id?: string };

type BillingRead = {
  subscription: SubscriptionRead | null;
  plan: PlanRead | null;
};

function displayDate(value?: string | null): string {
  if (!value) return 'Not provided by billing service';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Date unavailable' : parsed.toLocaleDateString();
}

/** Read-only until provider mode, entitlements and webhook replay are independently proven. */
export function BillingStatus() {
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

  return (
    <section aria-label="Billing status" className="space-y-4 py-4">
      <div className="rounded-lg bg-blue-50 border border-blue-200 p-4 text-sm text-blue-900">
        <ShieldCheck className="inline-block h-4 w-4 mr-1" aria-hidden="true" />
        Read-only account billing. Checkout, cancellation and payment changes are disabled
        pending verified Stripe provider, webhook and approval acceptance. No charge is initiated here.
      </div>
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
          <AlertTriangle className="inline-block w-4 h-4 mr-1" /> Billing service unavailable. Subscription status is unknown.
        </p>
      ) : !billing?.subscription ? (
        <p role="status" className="text-sm text-slate-600">
          No subscription record returned for this account. This does not confirm trial or payment eligibility.
        </p>
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-3 text-sm">
          <dt className="text-slate-500">Plan</dt>
          <dd className="font-medium text-slate-900">{billing.plan?.name ?? billing.plan?.slug ?? billing.plan?.id ?? 'Plan not identified'}</dd>
          <dt className="text-slate-500">Recorded status</dt>
          <dd className="font-medium text-slate-900">{billing.subscription.status ?? 'Unknown'}</dd>
          <dt className="text-slate-500">Current period ends</dt>
          <dd className="font-medium text-slate-900">{displayDate(billing.subscription.current_period_end)}</dd>
          <dt className="text-slate-500">Cancellation scheduled</dt>
          <dd className="font-medium text-slate-900">
            {billing.subscription.cancel_at_period_end === undefined ? 'Not reported' : billing.subscription.cancel_at_period_end ? 'Yes' : 'No'}
          </dd>
        </dl>
      )}
      <p className="text-xs text-slate-500">
        Status above is the backend subscription record, not confirmation of a current Stripe payment or webhook delivery.
      </p>
    </section>
  );
}
