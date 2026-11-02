import { useEffect, useState, type FormEvent } from 'react';
import clsx from 'clsx';
import { api } from '../../../lib/api';
import type { InsightMeta } from '../registry';
import {
  AdvisoryNotice, ErrorState, InsightShell, LoadingState, useInsight, type AdvisoryFields,
} from '../ui';

// Backend: GET/POST /api/insights/supply-chain-health require view_financials.
export const meta: InsightMeta = {
  slug: 'supply-chain-health',
  title: 'Supply chain health check',
  description: 'How fast stock and cash move through the business, and which step is the bottleneck.',
  group: 'Supply chain',
  roles: ['owner', 'manager'],
};

interface HealthResponse extends AdvisoryFields {
  measured: {
    days_inventory_outstanding: number | null;
    days_sales_outstanding: number | null;
    supplier_lead_time_days: number | null;
    supplier_lead_time_observations: number;
  };
  needs_input: { metric: string; label: string; why: string }[];
  prefill: {
    revenue: number | null;
    cogs: number | null;
    inventory_value: number | null;
    ar_balance: number | null;
    supplier_lead_times_days: number[];
  };
  coverage: { cogs_revenue_coverage_pct: number; inventory_items_missing_cost: number };
  summary: { rows_skipped: number };
}

interface Constraint {
  node_name: string;
  constraint_type: string;
  severity: string;
  utilization_pct: number;
  queue_depth: number;
  exploit_actions: string[];
  subordinate_actions: string[];
  elevate_actions: string[];
}

interface CheckResponse extends AdvisoryFields {
  scor_metrics: {
    responsiveness: { supplier_lead_time_days: number };
    cost: { supply_chain_cost_pct_revenue: number };
    assets: {
      cash_to_cash_cycle_days: number;
      days_inventory_outstanding: number;
      days_sales_outstanding: number;
      days_payable_outstanding: number;
      return_on_working_capital_pct: number;
    };
  };
  benchmarks: Record<string, string>;
  primary_constraint: Constraint | null;
  all_constraints: Constraint[];
  improvement_priority: string[];
}

const MONEY_FIELDS = [
  { name: 'revenue', label: 'Sales, last 12 months ($)' },
  { name: 'cogs', label: 'Cost of goods sold, last 12 months ($)' },
  { name: 'inventory_value', label: 'Stock on hand, at cost ($)' },
  { name: 'ar_balance', label: 'Owed to you by customers ($)' },
  { name: 'ap_balance', label: 'You owe suppliers ($)' },
  { name: 'supply_chain_costs', label: 'Warehousing, freight and handling, last 12 months ($)' },
] as const;
type MoneyField = typeof MONEY_FIELDS[number]['name'];

const AREAS = [
  { value: 'source', label: 'Buying & receiving' },
  { value: 'deliver', label: 'Picking & shipping' },
  { value: 'plan', label: 'Planning' },
  { value: 'return', label: 'Returns' },
  { value: 'enable', label: 'Admin & finance' },
  { value: 'make', label: 'Assembly / repacking' },
];

const CONSTRAINT_LABEL: Record<string, string> = {
  supplier_lead_time: 'Supplier delivery time',
  warehouse_throughput: 'Warehouse capacity',
  order_processing: 'Order handling',
  cash_flow: 'Cash flow',
  demand_planning: 'Demand planning',
  carrier_capacity: 'Carrier capacity',
  customs_clearance: 'Customs clearance',
  none_identified: 'General capacity',
};

const RATING_LABEL: Record<string, string> = {
  excellent: 'Excellent', good: 'Good', fair: 'Fair', poor: 'Needs work', insufficient_data: 'Not enough data',
};

const BENCHMARK_LABEL: Record<string, string> = {
  cash_to_cash: 'Cash-to-cash cycle',
  supply_chain_cost: 'Supply chain cost share',
  perfect_order_fulfillment: 'On-time, complete deliveries',
  order_fill_rate: 'Order lines filled',
  order_cycle_time: 'Order-to-delivery time',
};

interface StepRow { name: string; domain: string; throughput_per_day: string; backlog: string; utilization_pct: string; avg_cycle_time_hours: string }

const emptyStep = (): StepRow => ({
  name: '', domain: 'deliver', throughput_per_day: '', backlog: '', utilization_pct: '', avg_cycle_time_hours: '',
});

const inputClass = 'w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-300';

function days(value: number | null | undefined) {
  return value == null ? 'Not available' : `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} days`;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-xl font-bold text-slate-900 mt-1">{value}</div>
      {hint && <div className="text-xs text-slate-400 mt-1">{hint}</div>}
    </div>
  );
}

function ActionList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <div className="text-xs font-semibold text-slate-600">{title}</div>
      <ul className="list-disc pl-5 text-sm text-slate-700">
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </div>
  );
}

function CheckResult({ result }: { result: CheckResponse }) {
  const assets = result.scor_metrics.assets;
  const primary = result.primary_constraint;
  return (
    <div className="space-y-4" data-testid="check-result">
      <AdvisoryNotice source={result.source} />
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Cash-to-cash cycle" value={days(assets.cash_to_cash_cycle_days)}
          hint="Days between paying suppliers and getting paid by customers" />
        <Stat label="Days of stock" value={days(assets.days_inventory_outstanding)} />
        <Stat label="Supply chain cost share"
          value={`${result.scor_metrics.cost.supply_chain_cost_pct_revenue.toLocaleString(undefined, { maximumFractionDigits: 1 })}% of sales`} />
      </div>
      <div className="rounded-xl border border-slate-200 bg-white overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500 text-left">
            <tr><th className="px-4 py-2 font-medium">Measure</th><th className="px-4 py-2 font-medium">Compared with typical distributors</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {Object.entries(result.benchmarks).map(([key, rating]) => (
              <tr key={key}>
                <td className="px-4 py-2 text-slate-700">{BENCHMARK_LABEL[key] ?? key.replace(/_/g, ' ')}</td>
                <td className={clsx('px-4 py-2 font-medium', rating === 'poor' ? 'text-red-700' : rating === 'fair' ? 'text-amber-700' : 'text-slate-700')}>
                  {RATING_LABEL[rating] ?? rating}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-400">Comparisons use rough rule-of-thumb ranges, not verified industry data.</p>
      {primary ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 space-y-3">
          <div>
            <div className="text-xs text-amber-800">Main bottleneck</div>
            <div className="text-sm font-semibold text-slate-900">
              {primary.node_name} — {CONSTRAINT_LABEL[primary.constraint_type] ?? primary.constraint_type}
            </div>
            <div className="text-xs text-slate-600">
              {Math.round(primary.utilization_pct)}% busy, {primary.queue_depth.toLocaleString()} waiting ({primary.severity} severity)
            </div>
          </div>
          <ActionList title="Quick wins, no new spending" items={primary.exploit_actions} />
          <ActionList title="Pace other steps to this one" items={primary.subordinate_actions} />
          <ActionList title="If it is still the bottleneck: investments" items={primary.elevate_actions} />
          {result.all_constraints.length > 1 && (
            <p className="text-xs text-slate-600">
              Also stretched: {result.all_constraints.slice(1).map((c) => c.node_name).join(', ')}
            </p>
          )}
        </div>
      ) : (
        <p className="text-sm text-slate-500">No step is both over 80% busy and holding a backlog, so no bottleneck stands out.</p>
      )}
      {result.improvement_priority.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-slate-600">Where to focus first</div>
          <ol className="list-decimal pl-5 text-sm text-slate-700">
            {result.improvement_priority.map((item) => <li key={item}>{item.replace(/^\[[A-Z]+\]\s*/, '')}</li>)}
          </ol>
        </div>
      )}
    </div>
  );
}

function CheckForm({ prefill }: { prefill: HealthResponse['prefill'] | null }) {
  const [money, setMoney] = useState<Record<MoneyField, string>>({
    revenue: '', cogs: '', inventory_value: '', ar_balance: '', ap_balance: '', supply_chain_costs: '',
  });
  const [leadTimes, setLeadTimes] = useState('');
  const [steps, setSteps] = useState<StepRow[]>([emptyStep()]);
  const [result, setResult] = useState<CheckResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!prefill) return;
    // Fill only what the connected data measured; the user confirms or edits it.
    setMoney((prev) => {
      const next = { ...prev };
      for (const field of ['revenue', 'cogs', 'inventory_value', 'ar_balance'] as const) {
        const value = prefill[field];
        if (next[field] === '' && value != null) next[field] = String(value);
      }
      return next;
    });
    setLeadTimes((prev) => prev || prefill.supplier_lead_times_days.join(', '));
  }, [prefill]);

  const setStep = (index: number, field: keyof StepRow, value: string) =>
    setSteps((prev) => prev.map((step, i) => (i === index ? { ...step, [field]: value } : step)));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const figures = Object.fromEntries(MONEY_FIELDS.map(({ name }) => [name, Number(money[name])]));
    if (MONEY_FIELDS.some(({ name }) => money[name].trim() === '' || !Number.isFinite(figures[name]) || figures[name] < 0)) {
      setError('Fill in all six money figures (use 0 if none).');
      return;
    }
    const leadParts = leadTimes.split(/[\s,;]+/).filter(Boolean);
    const leadNumbers = leadParts.map(Number);
    if (leadNumbers.some((n) => !Number.isFinite(n) || n <= 0)) {
      setError('Supplier delivery times must be positive numbers separated by commas.');
      return;
    }
    const filled = steps.filter((step) => Object.entries(step).some(([key, value]) => key !== 'domain' && value.trim() !== ''));
    const processSteps = filled.map((step) => ({
      name: step.name.trim(),
      domain: step.domain,
      throughput_per_day: Number(step.throughput_per_day),
      backlog: Number(step.backlog),
      utilization_pct: Number(step.utilization_pct),
      avg_cycle_time_hours: Number(step.avg_cycle_time_hours),
    }));
    if (processSteps.some((step) => !step.name || [step.throughput_per_day, step.backlog, step.utilization_pct, step.avg_cycle_time_hours]
      .some((n) => !Number.isFinite(n)))) {
      setError('Complete every column for each process step, or clear the row.');
      return;
    }
    setBusy(true);
    setError(null);
    api.post<CheckResponse>('/api/insights/supply-chain-health/preview', {
      ...figures,
      supplier_lead_times_days: leadNumbers,
      process_steps: processSteps,
    })
      .then(setResult)
      .catch((err: unknown) => {
        setResult(null);
        setError(err instanceof Error ? err.message : 'The check failed.');
      })
      .finally(() => setBusy(false));
  };

  return (
    <section aria-label="Full health check" className="rounded-xl border border-slate-200 bg-white p-4 space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-slate-800">Full health check and bottleneck finder</h2>
        <p className="text-xs text-slate-500 mt-1">
          Figures from your connected data are filled in for you to confirm. Add the rest, and describe the busiest
          steps in your operation to find the bottleneck. Nothing is saved.
        </p>
      </div>
      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {MONEY_FIELDS.map(({ name, label }) => (
            <label key={name} className="text-xs text-slate-600 space-y-1">
              <span>{label}</span>
              <input name={name} inputMode="decimal" className={inputClass} value={money[name]}
                onChange={(event) => setMoney((prev) => ({ ...prev, [name]: event.target.value }))} />
            </label>
          ))}
        </div>
        <label className="block text-xs text-slate-600 space-y-1">
          <span>Days suppliers took to deliver, past orders (optional, e.g. 10, 14, 12)</span>
          <input name="supplier_lead_times_days" className={inputClass} value={leadTimes}
            onChange={(event) => setLeadTimes(event.target.value)} />
        </label>
        <fieldset className="space-y-2">
          <legend className="text-xs font-semibold text-slate-600">Process steps (optional)</legend>
          {steps.map((step, index) => (
            <div key={index} className="grid gap-2 sm:grid-cols-7 items-end" data-testid="process-step">
              <input aria-label="Step name" name={`step-${index}-name`} placeholder="e.g. Picking" className={clsx(inputClass, 'sm:col-span-2')}
                value={step.name} onChange={(event) => setStep(index, 'name', event.target.value)} />
              <select aria-label="Area" name={`step-${index}-domain`} className={inputClass} value={step.domain}
                onChange={(event) => setStep(index, 'domain', event.target.value)}>
                {AREAS.map((area) => <option key={area.value} value={area.value}>{area.label}</option>)}
              </select>
              <input aria-label="Handled per day" placeholder="Per day" name={`step-${index}-throughput`} inputMode="decimal" className={inputClass}
                value={step.throughput_per_day} onChange={(event) => setStep(index, 'throughput_per_day', event.target.value)} />
              <input aria-label="Waiting now" placeholder="Waiting" name={`step-${index}-backlog`} inputMode="decimal" className={inputClass}
                value={step.backlog} onChange={(event) => setStep(index, 'backlog', event.target.value)} />
              <input aria-label="How busy (%)" placeholder="Busy %" name={`step-${index}-utilization`} inputMode="decimal" className={inputClass}
                value={step.utilization_pct} onChange={(event) => setStep(index, 'utilization_pct', event.target.value)} />
              <input aria-label="Hours per item" placeholder="Hours each" name={`step-${index}-hours`} inputMode="decimal" className={inputClass}
                value={step.avg_cycle_time_hours} onChange={(event) => setStep(index, 'avg_cycle_time_hours', event.target.value)} />
            </div>
          ))}
          <div className="flex gap-3 text-xs">
            {steps.length < 20 && (
              <button type="button" className="font-semibold text-slate-700 underline" onClick={() => setSteps((prev) => [...prev, emptyStep()])}>
                Add a step
              </button>
            )}
            {steps.length > 1 && (
              <button type="button" className="text-slate-500 underline" onClick={() => setSteps((prev) => prev.slice(0, -1))}>
                Remove last step
              </button>
            )}
          </div>
        </fieldset>
        <button type="submit" disabled={busy} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-700 disabled:opacity-50">
          {busy ? 'Checking…' : 'Run health check'}
        </button>
      </form>
      {error && <ErrorState message={error} />}
      {result && <CheckResult result={result} />}
    </section>
  );
}

export default function SupplyChainHealthInsight() {
  const { data, error, loading, reload } = useInsight<HealthResponse>('/api/insights/supply-chain-health');

  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source={data?.source ?? 'connected_data'} />
      {loading && <LoadingState />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && !loading && (
        <section aria-label="Measured from your data" className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat label="Days of stock on hand" value={days(data.measured.days_inventory_outstanding)}
              hint={data.measured.days_inventory_outstanding == null ? 'Too few sales lines have a purchase cost' : undefined} />
            <Stat label="Days customers take to pay" value={days(data.measured.days_sales_outstanding)} />
            <Stat label="Days suppliers take to deliver" value={days(data.measured.supplier_lead_time_days)}
              hint={`${data.measured.supplier_lead_time_observations} received purchase orders`} />
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="text-xs font-semibold text-slate-600">Needs figures your systems don't hold</div>
            <ul className="mt-1 space-y-1 text-sm text-slate-700">
              {data.needs_input.map((item) => (
                <li key={item.metric}><span className="font-medium">{item.label}:</span> {item.why}</li>
              ))}
            </ul>
          </div>
          <p className="text-xs text-slate-500">
            Based on the last 12 months. {data.coverage.cogs_revenue_coverage_pct}% of sales could be matched to a purchase cost.
            {data.summary.rows_skipped > 0 && ` ${data.summary.rows_skipped} unreadable records were skipped.`}
          </p>
        </section>
      )}
      <CheckForm prefill={data?.prefill ?? null} />
    </InsightShell>
  );
}
