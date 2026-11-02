import { useState, type FormEvent } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { api } from '../../../lib/api';
import type { InsightMeta } from '../registry';
import {
  AdvisoryNotice, ErrorState, InsightShell, LoadingState, type AdvisoryFields,
} from '../ui';

export const meta: InsightMeta = {
  slug: 'onboarding-plan',
  title: 'New-hire onboarding plan',
  description: 'Build a dated first-90-days checklist for a new team member.',
  group: 'People',
  // Backend: require_permission("manage_users"); view_all also admits managers.
  roles: ['owner', 'manager'],
};

const DEPARTMENTS = [
  { value: 'sales', label: 'Sales' },
  { value: 'warehouse', label: 'Warehouse' },
  { value: 'delivery', label: 'Delivery / driving' },
  { value: 'office', label: 'Office / back office' },
  { value: 'management', label: 'Management' },
  { value: 'other', label: 'Other' },
] as const;

type TaskStatus = 'overdue' | 'due_soon' | 'upcoming' | 'completed' | 'source_review_required';

interface PlanItem {
  item_id: string;
  title: string;
  kind: string;
  owner: string;
  custom: boolean;
  days_from_start: number;
  due_on: string;
  status: TaskStatus;
  completed_on: string | null;
}

export interface OnboardingPlan extends AdvisoryFields {
  plan: {
    role_title: string | null;
    department: string;
    start_date: string;
    as_of: string;
    due_soon_days: number;
    items: PlanItem[];
    next_steps: string[];
  };
  summary: { total: number; overdue: number; due_soon: number; completed: number; upcoming: number };
}

interface CustomTask { title: string; days: string }

const STATUS_STYLE: Record<TaskStatus, { label: string; className: string }> = {
  overdue: { label: 'Overdue', className: 'text-red-700' },
  due_soon: { label: 'Due soon', className: 'text-amber-700' },
  upcoming: { label: 'Upcoming', className: 'text-slate-600' },
  completed: { label: 'Done', className: 'text-emerald-700' },
  source_review_required: { label: 'Needs checking', className: 'text-slate-500' },
};

const inputClass = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800';

function localDate(offsetDays = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function dayLabel(days: number) {
  if (days === 0) return 'Start day';
  return days < 0 ? `${-days} day(s) before start` : `Day ${days}`;
}

export default function OnboardingPlanPage() {
  const [roleTitle, setRoleTitle] = useState('');
  const [department, setDepartment] = useState('warehouse');
  const [startDate, setStartDate] = useState(localDate(7));
  const [includeStandard, setIncludeStandard] = useState(true);
  const [custom, setCustom] = useState<CustomTask[]>([]);
  const [completed, setCompleted] = useState<Record<string, string>>({});
  const [result, setResult] = useState<OnboardingPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function run(done: Record<string, string>) {
    setLoading(true);
    setError(null);
    try {
      const body = {
        role_title: roleTitle.trim() || undefined,
        department,
        start_date: startDate,
        include_standard: includeStandard,
        custom_tasks: custom
          .filter((t) => t.title.trim())
          .map((t) => ({ title: t.title.trim(), days_from_start: Number(t.days) || 0 })),
        completed: Object.entries(done).map(([item_id, completed_on]) => ({ item_id, completed_on })),
      };
      setResult(await api.post<OnboardingPlan>('/api/insights/onboarding-plan/preview', body));
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : 'The plan could not be built.');
    } finally {
      setLoading(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setCompleted({});
    void run({});
  }

  function toggleDone(item: PlanItem) {
    const next = { ...completed };
    if (next[item.item_id]) delete next[item.item_id];
    // The server dates the plan in UTC; a local "today" can be its tomorrow.
    else next[item.item_id] = new Date().toISOString().slice(0, 10);
    setCompleted(next);
    void run(next);
  }

  const updateTask = (index: number, patch: Partial<CustomTask>) =>
    setCustom((prev) => prev.map((t, i) => (i === index ? { ...t, ...patch } : t)));

  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source="user_supplied" />
      <p className="text-xs text-slate-500">Don't enter the person's name or contact details — the plan doesn't need them. Nothing is saved or sent.</p>
      <form onSubmit={submit} className="rounded-xl border border-slate-200 bg-white p-4 space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="text-xs font-medium text-slate-600 space-y-1">
            <span>Job title (optional)</span>
            <input aria-label="Job title" className={inputClass} maxLength={80} value={roleTitle}
              onChange={(e) => setRoleTitle(e.target.value)} placeholder="e.g. Warehouse associate" />
          </label>
          <label className="text-xs font-medium text-slate-600 space-y-1">
            <span>Team</span>
            <select aria-label="Team" className={inputClass} value={department} onChange={(e) => setDepartment(e.target.value)}>
              {DEPARTMENTS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
            </select>
          </label>
          <label className="text-xs font-medium text-slate-600 space-y-1">
            <span>Start date</span>
            <input aria-label="Start date" type="date" required className={inputClass} value={startDate}
              onChange={(e) => setStartDate(e.target.value)} />
          </label>
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={includeStandard} onChange={(e) => setIncludeStandard(e.target.checked)} />
          Include the standard checklist for this team
        </label>
        <div className="space-y-2">
          <div className="text-xs font-medium text-slate-600">Extra tasks</div>
          {custom.map((task, index) => (
            <div key={index} className="flex gap-2">
              <input aria-label={`Extra task ${index + 1}`} className={inputClass} maxLength={120} value={task.title}
                onChange={(e) => updateTask(index, { title: e.target.value })} placeholder="Task" />
              <input aria-label={`Days from start for task ${index + 1}`} type="number" min={-60} max={365}
                className="w-28 rounded-lg border border-slate-200 px-3 py-2 text-sm" value={task.days}
                onChange={(e) => updateTask(index, { days: e.target.value })} title="Days from start" />
              <button type="button" aria-label={`Remove task ${index + 1}`} className="text-slate-400 hover:text-slate-700"
                onClick={() => setCustom((prev) => prev.filter((_, i) => i !== index))}>
                <Trash2 size={16} />
              </button>
            </div>
          ))}
          {custom.length < 20 && (
            <button type="button" onClick={() => setCustom((prev) => [...prev, { title: '', days: '0' }])}
              className="inline-flex items-center gap-1 text-xs font-semibold text-slate-600 hover:text-slate-900">
              <Plus size={14} /> Add a task
            </button>
          )}
        </div>
        <button type="submit" disabled={loading}
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
          Build plan
        </button>
      </form>

      {loading && <LoadingState label="Building plan…" />}
      {error && <ErrorState message={error} />}
      {result && !loading && (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-4">
            {([
              ['Tasks', result.summary.total],
              ['Overdue', result.summary.overdue],
              ['Due this week', result.summary.due_soon],
              ['Done', result.summary.completed],
            ] as const).map(([label, value]) => (
              <div key={label} className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="text-xs text-slate-500">{label}</div>
                <div className="text-2xl font-bold text-slate-900">{value}</div>
              </div>
            ))}
          </div>
          {result.plan.next_steps.length > 0 && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              <div className="font-semibold">Do next</div>
              <ul className="list-disc pl-5">
                {result.plan.next_steps.map((step) => <li key={step}>{step}</li>)}
              </ul>
            </div>
          )}
          <div className="rounded-xl border border-slate-200 bg-white overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {['Done', 'Task', 'Who', 'When', 'Due', 'Status'].map((h) => (
                    <th key={h} className="text-left py-2.5 px-4 text-xs font-medium text-slate-500">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.plan.items.map((item) => {
                  const style = STATUS_STYLE[item.status] ?? STATUS_STYLE.upcoming;
                  return (
                    <tr key={item.item_id} className="border-b border-slate-100">
                      <td className="py-2.5 px-4">
                        <input type="checkbox" aria-label={`Mark done: ${item.title}`}
                          checked={item.status === 'completed'} disabled={loading}
                          onChange={() => toggleDone(item)} />
                      </td>
                      <td className="py-2.5 px-4 text-sm text-slate-800">{item.title}</td>
                      <td className="py-2.5 px-4 text-sm text-slate-600">{item.owner}</td>
                      <td className="py-2.5 px-4 text-xs text-slate-500">{dayLabel(item.days_from_start)}</td>
                      <td className="py-2.5 px-4 text-sm text-slate-700">{item.due_on}</td>
                      <td className={`py-2.5 px-4 text-sm font-medium ${style.className}`}>{style.label}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </InsightShell>
  );
}
