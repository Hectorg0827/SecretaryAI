import { useState, type FormEvent } from 'react';
import { api } from '../../../lib/api';
import type { InsightMeta } from '../registry';
import {
  AdvisoryNotice, ErrorState, InsightShell, LoadingState, type AdvisoryFields,
} from '../ui';

export const meta: InsightMeta = {
  slug: 'governance-check',
  title: 'Data access check',
  description: 'Test whether a role should be allowed to see, export or change specific kinds of business data.',
  group: 'Governance',
  // Backend: require_permission("approve_actions") → owner, manager.
  roles: ['owner', 'manager'],
};

const ROLES = [
  { value: 'viewer', label: 'Viewer (read only)' },
  { value: 'analyst', label: 'Analyst' },
  { value: 'manager', label: 'Manager' },
  { value: 'executive', label: 'Executive / owner' },
  { value: 'system_admin', label: 'System administrator' },
] as const;

const ACTIONS = [
  { value: 'view', label: 'View' },
  { value: 'draft', label: 'Draft' },
  { value: 'approve', label: 'Approve' },
  { value: 'execute', label: 'Carry out' },
  { value: 'export', label: 'Export / download' },
  { value: 'delete', label: 'Delete' },
  { value: 'configure', label: 'Change settings' },
] as const;

const COMMON_FIELDS = [
  { value: 'product_name', label: 'Product names' },
  { value: 'inventory_count', label: 'Stock counts' },
  { value: 'customer_name', label: 'Customer names' },
  { value: 'customer_email', label: 'Customer emails' },
  { value: 'contract_price', label: 'Contract prices' },
  { value: 'gross_margin', label: 'Margins' },
  { value: 'bank_account', label: 'Bank details' },
  { value: 'employee_salary', label: 'Salaries' },
] as const;

const DATA_TYPES = [
  { value: 'orders', label: 'Orders' },
  { value: 'invoices', label: 'Invoices' },
  { value: 'customer_pii', label: 'Customer personal data' },
  { value: 'action_audit_log', label: 'Action history' },
  { value: 'chat_conversations', label: 'Chat conversations' },
  { value: 'supplier_agreements', label: 'Supplier agreements' },
] as const;

interface FieldResult {
  field: string;
  data_level: string;
  allowed: boolean;
  reason: string;
}

interface Anomaly {
  anomaly_type: string;
  severity: string;
  description: string;
  recommended_action: string;
  would_block: boolean;
}

interface Retention {
  data_type: string;
  retention_years?: number;
  retention_days?: number;
  basis: string;
  auto_purge: boolean;
  legal_hold_capable: boolean;
}

export interface GovernanceResult extends AdvisoryFields {
  role: string;
  action: string;
  decision: 'allowed' | 'blocked';
  highest_data_level: string;
  fields: FieldResult[];
  blocked_fields: number;
  anomalies: Anomaly[];
  role_authority: Record<string, string>;
  retention: Retention[];
}

const inputClass = 'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800';

function optionalNumber(value: string): number | undefined {
  if (value.trim() === '') return undefined;
  const n = Number(value);
  return Number.isInteger(n) ? n : undefined;
}

export default function GovernanceCheckPage() {
  const [role, setRole] = useState('analyst');
  const [action, setAction] = useState('export');
  const [fields, setFields] = useState<string[]>(['customer_email']);
  const [otherFields, setOtherFields] = useState('');
  const [dataTypes, setDataTypes] = useState<string[]>([]);
  const [exports, setExports] = useState('');
  const [hour, setHour] = useState('');
  const [result, setResult] = useState<GovernanceResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  async function submit(event: FormEvent) {
    event.preventDefault();
    const extra = otherFields.split(',').map((f) => f.trim()).filter(Boolean);
    const allFields = [...new Set([...fields, ...extra])];
    if (allFields.length === 0) {
      setError('Choose at least one kind of data.');
      return;
    }
    const activity = {
      exports_last_hour: optionalNumber(exports),
      access_hour: optionalNumber(hour),
    };
    const hasActivity = Object.values(activity).some((v) => v !== undefined);
    setLoading(true);
    setError(null);
    try {
      const body = {
        role, action, fields: allFields, data_types: dataTypes,
        ...(hasActivity ? { activity } : {}),
      };
      setResult(await api.post<GovernanceResult>('/api/insights/governance-check/preview', body));
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : 'The check could not be run.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <InsightShell title={meta.title} description={meta.description}>
      <AdvisoryNotice source="user_supplied" />
      <form onSubmit={submit} className="rounded-xl border border-slate-200 bg-white p-4 space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs font-medium text-slate-600 space-y-1">
            <span>Who</span>
            <select aria-label="Who" className={inputClass} value={role} onChange={(e) => setRole(e.target.value)}>
              {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
          </label>
          <label className="text-xs font-medium text-slate-600 space-y-1">
            <span>Wants to</span>
            <select aria-label="Wants to" className={inputClass} value={action} onChange={(e) => setAction(e.target.value)}>
              {ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
          </label>
        </div>
        <fieldset className="space-y-2">
          <legend className="text-xs font-medium text-slate-600">Which data</legend>
          <div className="grid gap-1 sm:grid-cols-4">
            {COMMON_FIELDS.map((f) => (
              <label key={f.value} className="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" checked={fields.includes(f.value)}
                  onChange={() => setFields((prev) => toggle(prev, f.value))} />
                {f.label}
              </label>
            ))}
          </div>
          <input aria-label="Other data (comma separated)" className={inputClass} placeholder="Other data, comma separated (e.g. sku, ar_balance)"
            value={otherFields} onChange={(e) => setOtherFields(e.target.value)} />
        </fieldset>
        <fieldset className="space-y-2">
          <legend className="text-xs font-medium text-slate-600">How long should we keep… (optional)</legend>
          <div className="grid gap-1 sm:grid-cols-3">
            {DATA_TYPES.map((d) => (
              <label key={d.value} className="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" checked={dataTypes.includes(d.value)}
                  onChange={() => setDataTypes((prev) => toggle(prev, d.value))} />
                {d.label}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs font-medium text-slate-600 space-y-1">
            <span>Downloads in the last hour (optional)</span>
            <input aria-label="Downloads in the last hour" type="number" min={0} className={inputClass}
              value={exports} onChange={(e) => setExports(e.target.value)} />
          </label>
          <label className="text-xs font-medium text-slate-600 space-y-1">
            <span>Hour of access, 0–23 (optional)</span>
            <input aria-label="Hour of access" type="number" min={0} max={23} className={inputClass}
              value={hour} onChange={(e) => setHour(e.target.value)} />
          </label>
        </div>
        <button type="submit" disabled={loading}
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
          Check access
        </button>
      </form>

      {loading && <LoadingState label="Checking…" />}
      {error && <ErrorState message={error} />}
      {result && !loading && (
        <div className="space-y-4">
          <div className={`rounded-xl border p-4 ${result.decision === 'allowed'
            ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
            : 'border-red-200 bg-red-50 text-red-800'}`}>
            <div className="text-sm font-semibold">
              {result.decision === 'allowed'
                ? 'Allowed by the policy'
                : `Not allowed: ${result.blocked_fields} kind(s) of data are above this role's limit`}
            </div>
            <div className="text-xs mt-1">Most sensitive data in this request: {result.highest_data_level}. This check does not change anyone's access.</div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {['Data', 'Sensitivity', 'Allowed?', 'Why'].map((h) => (
                    <th key={h} className="text-left py-2.5 px-4 text-xs font-medium text-slate-500">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.fields.map((f) => (
                  <tr key={f.field} className="border-b border-slate-100">
                    <td className="py-2.5 px-4 text-sm text-slate-800">{f.field}</td>
                    <td className="py-2.5 px-4 text-sm text-slate-700 capitalize">{f.data_level}</td>
                    <td className={`py-2.5 px-4 text-sm font-medium ${f.allowed ? 'text-emerald-700' : 'text-red-700'}`}>
                      {f.allowed ? 'Yes' : 'No'}
                    </td>
                    <td className="py-2.5 px-4 text-xs text-slate-500">{f.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.anomalies.length > 0 && (
            <div className="rounded-xl border border-amber-200 bg-white p-4 space-y-2">
              <div className="text-sm font-semibold text-slate-800">Warning signs</div>
              {result.anomalies.map((a) => (
                <div key={a.anomaly_type} className="text-sm text-slate-700">
                  <span className="font-medium capitalize">{a.severity}:</span> {a.description}
                  <div className="text-xs text-slate-500">Suggested: {a.recommended_action}</div>
                </div>
              ))}
            </div>
          )}
          {result.retention.length > 0 && (
            <div className="rounded-xl border border-slate-200 bg-white p-4 space-y-1">
              <div className="text-sm font-semibold text-slate-800">How long to keep it</div>
              {result.retention.map((r) => (
                <div key={r.data_type} className="text-sm text-slate-700">
                  {DATA_TYPES.find((d) => d.value === r.data_type)?.label ?? r.data_type}:{' '}
                  {r.retention_years !== undefined ? `${r.retention_years} years` : `${r.retention_days} days`}
                  <span className="text-xs text-slate-500"> — {r.basis}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </InsightShell>
  );
}
