/** Money customers owe, from GET /api/insights/receivables (view_financials). */
import { useState, type FormEvent } from 'react';
import type { InsightMeta } from '../registry';
import {
  AdvisoryNotice, ErrorState, InsightShell, LoadingState, useInsight, type AdvisoryFields,
} from '../ui';

export const meta: InsightMeta = {
  slug: 'receivables',
  title: 'Money owed to you',
  description: 'Unpaid invoices grouped by how overdue they are, and which customers to follow up with first.',
  group: 'Finance',
  // view_financials: owner and manager only.
  roles: ['owner', 'manager'],
};

type Bucket = 'current' | 'days_1_30' | 'days_31_60' | 'days_61_90' | 'days_91_plus' | 'unaged';
type Priority = 'high' | 'medium' | 'low';

export interface ReceivableAccount {
  customer_id: string;
  name: string;
  total_owed: number;
  overdue: number;
  buckets: Record<Bucket, number>;
  open_invoices: number;
  oldest_days_overdue: number | null;
  priority: Priority | null;
  next_step: string;
  quickbooks_balance: number | null;
  balance_matches_quickbooks: boolean | null;
}

export interface NotAgedAccount {
  customer_id: string;
  name: string;
  quickbooks_balance: number | null;
  reason: string;
}

export interface ReceivablesResponse extends AdvisoryFields {
  as_of: string;
  currency: string | null;
  aging_status: 'available_unreconciled' | 'partial_aging' | 'unavailable';
  exceptions: string[];
  total_owed: number | null;
  overdue_total: number | null;
  buckets: Record<Bucket, number> | null;
  open_invoice_count?: number;
  accounts_total?: number;
  accounts?: ReceivableAccount[];
  not_aged?: NotAgedAccount[];
  truncated?: boolean;
  rows_skipped?: number;
  rows_unattributed?: number;
}

export const CURRENCY_KEY = 'secretary_receivables_currency';
const CURRENCIES = ['USD', 'CAD', 'MXN', 'EUR', 'GBP', 'AUD'];

const BUCKET_LABELS: [Bucket, string][] = [
  ['current', 'Not yet due'],
  ['days_1_30', '1–30 days overdue'],
  ['days_31_60', '31–60 days overdue'],
  ['days_61_90', '61–90 days overdue'],
  ['days_91_plus', 'Over 90 days overdue'],
  ['unaged', 'No due date'],
];

const PRIORITY_CFG: Record<Priority, { label: string; badge: string }> = {
  high:   { label: 'Call first', badge: 'bg-red-50 text-red-700 border-red-200' },
  medium: { label: 'Follow up',  badge: 'bg-amber-50 text-amber-700 border-amber-200' },
  low:    { label: 'Reminder',   badge: 'bg-sky-50 text-sky-700 border-sky-200' },
};

const EXCEPTION_TEXT: Record<string, string> = {
  currency_unverified: 'the currency has not been confirmed',
  source_incomplete: 'QuickBooks did not return all invoices',
  history_not_exhaustive: 'older open invoices may be missing',
  snapshot_date_mismatch: 'the QuickBooks data is from a different day',
  source_stale: 'the QuickBooks data is out of date',
};

function readSavedCurrency(): string | null {
  try {
    const saved = localStorage.getItem(CURRENCY_KEY);
    return saved && /^[A-Z]{3}$/.test(saved) ? saved : null;
  } catch {
    return null;
  }
}

function saveCurrency(currency: string) {
  try {
    localStorage.setItem(CURRENCY_KEY, currency);
  } catch {
    // Remembering the choice is a convenience; the page works without it.
  }
}

function moneyFormatter(currency: string) {
  const format = new Intl.NumberFormat('en-US', { style: 'currency', currency });
  return (value: number | null | undefined) => (value == null ? '—' : format.format(value));
}

function CurrencyPrompt({ initial, onConfirm }: { initial: string; onConfirm: (currency: string) => void }) {
  const [choice, setChoice] = useState(initial);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onConfirm(choice);
  };
  return (
    <form onSubmit={submit} className="rounded-xl border border-slate-200 bg-white p-5 space-y-3 max-w-lg">
      <h2 className="text-sm font-semibold text-slate-800">Which currency are your QuickBooks invoices in?</h2>
      <p className="text-xs text-slate-500">
        SecretaryAI cannot read the currency from QuickBooks yet, so please confirm it once.
        Amounts are shown exactly as QuickBooks records them; nothing is converted.
      </p>
      <div className="flex gap-2 items-center">
        <label htmlFor="receivables-currency" className="text-sm text-slate-600">Currency</label>
        <select
          id="receivables-currency"
          value={choice}
          onChange={(event) => setChoice(event.target.value)}
          className="rounded-lg border border-slate-200 px-2 py-1 text-sm"
        >
          {CURRENCIES.map((code) => <option key={code} value={code}>{code}</option>)}
        </select>
        <button type="submit" className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700">
          Show money owed
        </button>
      </div>
    </form>
  );
}

function ReceivablesView({ data, currency }: { data: ReceivablesResponse; currency: string }) {
  const money = moneyFormatter(currency);
  if (data.aging_status === 'unavailable' || !data.buckets) {
    const reasons = data.exceptions.map((code) => EXCEPTION_TEXT[code] ?? code).join('; ');
    return (
      <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
        Amounts owed could not be worked out reliably{reasons ? `: ${reasons}` : ''}. No totals are shown rather than possibly wrong ones.
      </div>
    );
  }
  const buckets = data.buckets;
  const accounts = data.accounts ?? [];
  const toCollect = accounts.filter((a) => a.priority);
  const notDue = accounts.filter((a) => !a.priority);
  const notAged = data.not_aged ?? [];
  const over60 = buckets.days_61_90 + buckets.days_91_plus;

  return (
    <div className="space-y-5">
      {(data.rows_unattributed ?? 0) > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {data.rows_unattributed} invoice(s) could not be read and had no customer attached. They are not in the
          totals below, so the real amount owed may be higher.
        </div>
      )}
      <div className="grid gap-3 grid-cols-2 sm:grid-cols-4">
        {[
          ['Total owed to you', data.total_owed],
          ['Overdue', data.overdue_total],
          ['Not yet due', buckets.current],
          ['More than 60 days overdue', over60],
        ].map(([label, value]) => (
          <div key={label as string} className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="text-xl font-bold text-slate-900">{money(value as number | null)}</div>
            <div className="text-xs text-slate-500 mt-1">{label}</div>
          </div>
        ))}
      </div>

      <section aria-label="How overdue" className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-800 mb-3">How overdue the money is</h2>
        <dl className="grid gap-2 grid-cols-2 sm:grid-cols-6">
          {BUCKET_LABELS.map(([bucket, label]) => (
            <div key={bucket}>
              <dt className="text-xs text-slate-500">{label}</dt>
              <dd className="text-sm font-semibold text-slate-800">{money(buckets[bucket])}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section aria-label="Who to follow up with first" className="rounded-xl border border-slate-200 bg-white overflow-hidden">
        <h2 className="px-5 py-3 text-sm font-semibold text-slate-800 border-b border-slate-100">Who to follow up with first</h2>
        {toCollect.length === 0 ? (
          <p className="px-5 py-4 text-sm text-slate-500">No customer is overdue right now.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-slate-100">
                <tr>
                  {['Customer', 'Priority', 'Overdue', 'Total owed', 'Oldest', 'Suggested next step'].map((h) => (
                    <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {toCollect.map((a) => (
                  <tr key={a.customer_id} className="align-top">
                    <td className="px-5 py-3 text-sm font-medium text-slate-800">
                      {a.name}
                      {a.balance_matches_quickbooks === false && (
                        <div className="text-xs font-normal text-amber-700">
                          QuickBooks balance is {money(a.quickbooks_balance)}; it may include credits or unapplied payments.
                        </div>
                      )}
                    </td>
                    <td className="px-5 py-3">
                      {a.priority && (
                        <span className={`inline-block rounded-full border px-2 py-0.5 text-xs font-medium ${PRIORITY_CFG[a.priority].badge}`}>
                          {PRIORITY_CFG[a.priority].label}
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-sm text-slate-800 whitespace-nowrap">{money(a.overdue)}</td>
                    <td className="px-5 py-3 text-sm text-slate-600 whitespace-nowrap">{money(a.total_owed)}</td>
                    <td className="px-5 py-3 text-sm text-slate-600 whitespace-nowrap">
                      {a.oldest_days_overdue ? `${a.oldest_days_overdue} days` : '—'}
                    </td>
                    <td className="px-5 py-3 text-sm text-slate-700">{a.next_step}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {notDue.length > 0 && (
        <section aria-label="Owing but not overdue" className="rounded-xl border border-slate-200 bg-white p-5 space-y-2">
          <h2 className="text-sm font-semibold text-slate-800">Owing, but nothing overdue</h2>
          <ul className="divide-y divide-slate-50">
            {notDue.map((a) => (
              <li key={a.customer_id} className="py-2 text-sm flex justify-between gap-3">
                <span><span className="font-medium text-slate-800">{a.name}</span> <span className="text-slate-500">— {a.next_step}</span></span>
                <span className="text-slate-700 whitespace-nowrap">{money(a.total_owed)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {notAged.length > 0 && (
        <section aria-label="Could not be checked" className="rounded-xl border border-slate-200 bg-white p-5 space-y-2">
          <h2 className="text-sm font-semibold text-slate-800">Could not be checked (insufficient data)</h2>
          <ul className="divide-y divide-slate-50">
            {notAged.map((c) => (
              <li key={c.customer_id} className="py-2 text-sm">
                <span className="font-medium text-slate-800">{c.name}</span>
                <span className="text-slate-500"> — {c.reason} QuickBooks balance: {money(c.quickbooks_balance)}.</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="text-xs text-slate-400 space-y-1">
        {data.exceptions.includes('missing_due_date') && (
          <p>Some invoices have no due date in QuickBooks, so they are shown under “No due date”.</p>
        )}
        {(data.rows_skipped ?? 0) - (data.rows_unattributed ?? 0) > 0 && (
          <p>
            {(data.rows_skipped ?? 0) - (data.rows_unattributed ?? 0)} invoice(s) could not be read; their customers
            are listed under “Could not be checked”.
          </p>
        )}
        {data.truncated && <p>Only the first customers in each list are shown.</p>}
        <p>Totals come from open invoices in QuickBooks. They are not reconciled to payments or your general ledger.</p>
      </div>
    </div>
  );
}

export default function Receivables() {
  const [currency, setCurrency] = useState<string | null>(readSavedCurrency);
  const [lastCurrency, setLastCurrency] = useState<string>(currency ?? 'USD');
  const { data, error, loading, reload } = useInsight<ReceivablesResponse>(
    currency ? `/api/insights/receivables?currency=${currency}` : null,
  );

  const confirm = (choice: string) => {
    saveCurrency(choice);
    setLastCurrency(choice);
    setCurrency(choice);
  };

  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source={data?.source} />
      {!currency ? (
        <CurrencyPrompt initial={lastCurrency} onConfirm={confirm} />
      ) : (
        <>
          <p className="text-xs text-slate-500">
            Amounts in {currency}, as you confirmed.{' '}
            <button type="button" onClick={() => setCurrency(null)} className="font-semibold underline">Change</button>
          </p>
          {loading && <LoadingState />}
          {error && <ErrorState message={error} onRetry={reload} />}
          {data && !loading && data.currency === currency && <ReceivablesView data={data} currency={currency} />}
        </>
      )}
    </InsightShell>
  );
}
