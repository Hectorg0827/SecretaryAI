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

  return <div className="h-full overflow-y-auto bg-[#0e1527] p-5 text-[#eff0fa] md:p-8">
    <div className="mx-auto max-w-6xl space-y-7">
      <header>
        <h1 className="text-3xl font-semibold tracking-[-0.035em] text-[#f5f2ff]">Lola · Finance Workspace</h1>
        <p className="mt-2 text-sm text-[#a9b7cf]">Secure, department-scoped financial intelligence · Read-only staging evidence.</p>
      </header>
      {error && <div role="alert" className="rounded-xl border border-[#916a37] bg-[#3a2a24] p-4 text-[#fce7c2]">
        Finance evidence is unavailable or your current access could not be verified. No zero balances are implied.
      </div>}
      {!error && !data && <p role="status">Checking your authorized finance summaries…</p>}
      {data && <>
        <p className="text-xs text-[#a6b4cf]">Snapshot {new Date(data.snapshot_generated_at).toLocaleString()} · Evidence is partial; currency and ledger reconciliation are not certified.</p>
        <div className="grid gap-4 md:grid-cols-2">
          {data.authorized_scopes.map((scope) => {
            const summary = data.sections[scope];
            if (!summary) return null;
            return <section key={scope} className="rounded-2xl border border-[#3d4a68] bg-[linear-gradient(150deg,#1c2740,#131c30)] p-6 shadow-[0_12px_40px_rgba(0,0,0,.18)]" aria-label={titles[scope]}>
              <div className="flex items-center justify-between gap-3">
                <h2 className="font-semibold text-[#f3e7d1]">{titles[scope]}</h2>
                <span className="rounded-full border border-[#4b5973] bg-[#25324c] px-3 py-1 text-xs font-medium text-[#cbd5e9]">{summary.status.replace('_', ' ')}</span>
              </div>
              {summary.status === 'observed' && (scope === 'ar' || scope === 'ap') && <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div><dt className="text-[#a7b6cf]">Open</dt><dd className="text-xl font-semibold text-white">{summary.open_count}</dd></div>
                <div><dt className="text-[#a7b6cf]">Overdue</dt><dd className="text-xl font-semibold text-white">{summary.overdue_count}</dd></div>
                <div><dt className="text-[#a7b6cf]">Missing due date</dt><dd className="text-xl font-semibold text-white">{summary.due_date_missing_count}</dd></div>
                <div><dt className="text-[#a7b6cf]">Examined</dt><dd className="text-xl font-semibold text-white">{summary.headers_examined}</dd></div>
              </dl>}
              {summary.status === 'observed' && scope === 'ledger' && <p className="mt-4 text-sm text-[#d3deef]">{summary.variance_detected ? 'Unreconciled variance detected' : 'No variance detected in this limited comparison'}</p>}
              <p className="mt-4 text-sm leading-6 text-[#aab9d1]">{summary.note}</p>
            </section>;
          })}
        </div>
        <p className="rounded-xl border border-[#806341] bg-[#302b2b] p-4 text-sm leading-6 text-[#f4d9ae]">
          This workspace cannot post payments, approve bills, change prices or send customer statements. Those workflows require separate controls and acceptance tests.
        </p>
      </>}
    </div>
  </div>;
}
