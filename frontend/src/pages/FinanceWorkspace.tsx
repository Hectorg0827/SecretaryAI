import { useEffect, useState } from 'react';
import { api } from '../lib/api';

type Summary = {
  status: 'observed' | 'unavailable' | 'not_assessed';
  headers_examined: number | null;
  open_count: number | null;
  overdue_count: number | null;
  due_date_missing_count: number | null;
  variance_detected: boolean | null;
  note: string;
};
type FinanceWorkspaceData = {
  role: string;
  authorized_scopes: Array<'ar' | 'ap' | 'ledger' | 'pricing'>;
  snapshot_generated_at: string;
  source_quality: 'partial';
  currency_verified: false;
  ledger_reconciled: false;
  sections: Partial<Record<'ar' | 'ap' | 'ledger' | 'pricing', Summary>>;
};
const titles: Record<string, string> = {
  ar: 'Accounts Receivable', ap: 'Accounts Payable',
  ledger: 'Financial Reporting', pricing: 'Pricing & Billbacks',
};

export function FinanceWorkspace() {
  const [data, setData] = useState<FinanceWorkspaceData | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    api.get<FinanceWorkspaceData>('/finance/workspace')
      .then((result) => { if (active) setData(result); })
      .catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, []);

  return <div className="h-full overflow-y-auto bg-slate-50 p-5 md:p-8">
    <div className="mx-auto max-w-5xl space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-slate-900">Lola · Finance Workspace</h1>
        <p className="mt-1 text-sm text-slate-600">Department-scoped, read-only sandbox evidence.</p>
      </header>
      {error && <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-amber-950">
        Finance evidence is unavailable or your current access could not be verified. No zero balances are implied.
      </div>}
      {!error && !data && <p role="status">Checking your authorized finance summaries…</p>}
      {data && <>
        <p className="text-xs text-slate-500">Snapshot {new Date(data.snapshot_generated_at).toLocaleString()} · Evidence is partial; currency and ledger reconciliation are not certified.</p>
        <div className="grid gap-4 md:grid-cols-2">
          {data.authorized_scopes.map((scope) => {
            const summary = data.sections[scope];
            if (!summary) return null;
            return <section key={scope} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm" aria-label={titles[scope]}>
              <div className="flex items-center justify-between gap-3">
                <h2 className="font-semibold text-slate-900">{titles[scope]}</h2>
                <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700">{summary.status.replace('_', ' ')}</span>
              </div>
              {summary.status === 'observed' && (scope === 'ar' || scope === 'ap') && <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div><dt className="text-slate-500">Open</dt><dd className="text-xl font-semibold">{summary.open_count}</dd></div>
                <div><dt className="text-slate-500">Overdue</dt><dd className="text-xl font-semibold">{summary.overdue_count}</dd></div>
                <div><dt className="text-slate-500">Missing due date</dt><dd className="text-xl font-semibold">{summary.due_date_missing_count}</dd></div>
                <div><dt className="text-slate-500">Examined</dt><dd className="text-xl font-semibold">{summary.headers_examined}</dd></div>
              </dl>}
              {summary.status === 'observed' && scope === 'ledger' && <p className="mt-4 text-sm text-slate-700">{summary.variance_detected ? 'Unreconciled variance detected' : 'No variance detected in this limited comparison'}</p>}
              <p className="mt-4 text-sm text-slate-600">{summary.note}</p>
            </section>;
          })}
        </div>
        <p className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          This workspace cannot post payments, approve bills, change prices or send customer statements. Those workflows require separate controls and acceptance tests.
        </p>
      </>}
    </div>
  </div>;
}
