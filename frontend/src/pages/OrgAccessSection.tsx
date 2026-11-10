import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../lib/api';

type Level = 'executive' | 'department_manager' | 'employee';

export interface OrgProfile {
  org_level: Level;
  department: string;
  manager_user_id: string | null;
  territories: string[];
  financial_clearance: boolean;
  hr_clearance: boolean;
  action_limit: string | null;
  updated_at?: string | null;
}

export interface OrgMember {
  user_id: string;
  name: string | null;
  email: string | null;
  role: string;
  active: boolean;
  profile: OrgProfile | null;
  reports_to_name: string | null;
  allowed_levels: Level[];
}

interface OrgResponse { members: OrgMember[]; departments: string[]; levels: Level[] }

const LEVEL_LABEL: Record<Level, string> = {
  executive: 'Executive (whole company)',
  department_manager: 'Department manager',
  employee: 'Employee',
};

const label = (s: string) => s.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());

/** Plain-language summary of what Lola will let this person see and do. */
export function describeAccess(m: OrgMember, members: OrgMember[]): string {
  const p = m.profile;
  if (!p) {
    return m.role === 'owner' || m.role === 'manager'
      ? 'Default for their role: sees the whole company.'
      : 'Default for their role: sees only accounts assigned to them.';
  }
  const parts = [LEVEL_LABEL[p.org_level]];
  if (p.department !== 'general') parts.push(label(p.department));
  const mgr = members.find((x) => x.user_id === p.manager_user_id);
  if (mgr) parts.push(`reports to ${mgr.name ?? mgr.email}`);
  if (p.territories.length) parts.push(`territories: ${p.territories.join(', ')}`);
  if (p.financial_clearance) parts.push('finance clearance');
  if (p.hr_clearance) parts.push('HR clearance');
  if (p.action_limit != null) parts.push(`approval limit $${Number(p.action_limit).toLocaleString()}`);
  return parts.join(' · ');
}

function blank(m: OrgMember): OrgProfile {
  return {
    org_level: m.role === 'owner' ? 'executive' : 'employee', department: 'general', manager_user_id: null,
    territories: [], financial_clearance: false, hr_clearance: false, action_limit: null,
  };
}

function PersonEditor({ member, members, departments, onSaved, onCancel }: {
  member: OrgMember; members: OrgMember[]; departments: string[];
  onSaved: () => void; onCancel: () => void;
}) {
  const [form, setForm] = useState<OrgProfile>(member.profile ?? blank(member));
  const [territories, setTerritories] = useState((member.profile?.territories ?? []).join(', '));
  const [limit, setLimit] = useState(member.profile?.action_limit ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const id = member.user_id;
  const managers = members.filter((m) => m.active && m.user_id !== id);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    const amount = String(limit).trim();
    if (amount !== '' && !/^\d{1,9}(\.\d{1,2})?$/.test(amount)) {
      setError('Approval limit must be an amount like 5000 or 2500.50');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await api.put(`/api/lola/org/${encodeURIComponent(id)}`, {
        ...form,
        manager_user_id: form.manager_user_id || null,
        territories: territories.split(',').map((t) => t.trim()).filter(Boolean),
        action_limit: amount === '' ? null : amount,
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    setBusy(true);
    setError('');
    try {
      await api.delete(`/api/lola/org/${encodeURIComponent(id)}`);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not reset');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={save} aria-label={`Edit access for ${member.name ?? member.email}`}
      className="mt-3 grid gap-3 rounded-lg border border-slate-200 bg-slate-50 p-4 sm:grid-cols-2">
      <label className="text-sm font-medium" htmlFor={`lvl-${id}`}>Level
        <select id={`lvl-${id}`} value={form.org_level} className="mt-1 w-full rounded-lg border px-3 py-2 font-normal"
          onChange={(e) => setForm({ ...form, org_level: e.target.value as Level })}>
          {member.allowed_levels.map((l) => <option key={l} value={l}>{LEVEL_LABEL[l]}</option>)}
        </select>
      </label>
      <label className="text-sm font-medium" htmlFor={`dept-${id}`}>Department
        <select id={`dept-${id}`} value={form.department} className="mt-1 w-full rounded-lg border px-3 py-2 font-normal"
          onChange={(e) => setForm({ ...form, department: e.target.value })}>
          {departments.map((d) => <option key={d} value={d}>{label(d)}</option>)}
        </select>
      </label>
      <label className="text-sm font-medium" htmlFor={`mgr-${id}`}>Reports to
        <select id={`mgr-${id}`} value={form.manager_user_id ?? ''} className="mt-1 w-full rounded-lg border px-3 py-2 font-normal"
          onChange={(e) => setForm({ ...form, manager_user_id: e.target.value || null })}>
          <option value="">Nobody</option>
          {managers.map((m) => <option key={m.user_id} value={m.user_id}>{m.name ?? m.email}</option>)}
        </select>
      </label>
      <label className="text-sm font-medium" htmlFor={`terr-${id}`}>Territories
        <input id={`terr-${id}`} value={territories} placeholder="e.g. West, Northeast" maxLength={2000}
          onChange={(e) => setTerritories(e.target.value)} className="mt-1 w-full rounded-lg border px-3 py-2 font-normal" />
      </label>
      <label className="text-sm font-medium" htmlFor={`limit-${id}`}>Approval limit (USD, blank = no limit set)
        <input id={`limit-${id}`} inputMode="decimal" value={limit} placeholder="e.g. 5000"
          onChange={(e) => setLimit(e.target.value)} className="mt-1 w-full rounded-lg border px-3 py-2 font-normal" />
      </label>
      <fieldset className="text-sm">
        <legend className="font-medium">Clearances</legend>
        <label className="mt-1 flex items-center gap-2">
          <input type="checkbox" checked={form.financial_clearance}
            onChange={(e) => setForm({ ...form, financial_clearance: e.target.checked })} />
          Finance (balances, costs, receivables)
        </label>
        <label className="mt-1 flex items-center gap-2">
          <input type="checkbox" checked={form.hr_clearance}
            onChange={(e) => setForm({ ...form, hr_clearance: e.target.checked })} />
          HR (only takes effect in the HR department)
        </label>
      </fieldset>
      {error && <p role="alert" className="text-sm text-red-700 sm:col-span-2">{error}</p>}
      <div className="flex flex-wrap gap-2 sm:col-span-2">
        <button type="submit" disabled={busy}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
          {busy ? 'Saving…' : 'Save'}
        </button>
        <button type="button" onClick={onCancel} disabled={busy}
          className="rounded-lg border px-4 py-2 text-sm">Cancel</button>
        {member.profile && (
          <button type="button" onClick={reset} disabled={busy}
            className="ml-auto rounded-lg border border-red-200 px-4 py-2 text-sm text-red-700">
            Reset to role defaults
          </button>
        )}
      </div>
    </form>
  );
}

/** Owner-only: the hierarchy and clearances Lola enforces. The server re-checks everything. */
export function OrgAccessSection() {
  const [data, setData] = useState<OrgResponse | null>(null);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [saved, setSaved] = useState('');

  const load = useCallback(() => {
    api.get<OrgResponse>('/api/lola/org').then((r) => { setData(r); setError(''); })
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not load the org chart.'));
  }, []);
  useEffect(load, [load]);

  const members = useMemo(() => data?.members ?? [], [data]);

  return (
    <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5">
      <div>
        <h2 className="font-semibold text-slate-800">Org &amp; access</h2>
        <p className="mt-1 text-sm text-slate-600">
          Who reports to whom, departments, territories and clearances. Lola uses this to decide what each person
          can see and approve. Changes apply on their next question. Nobody can be given more than their role allows.
        </p>
      </div>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {saved && <p role="status" className="text-sm text-emerald-700">{saved}</p>}
      {!data && !error && <p className="text-sm text-slate-500">Loading…</p>}
      <ul className="divide-y divide-slate-100">
        {members.map((m) => (
          <li key={m.user_id} className="py-3">
            <div className="flex flex-wrap items-start justify-between gap-2 text-sm">
              <div>
                <p className="font-medium text-slate-900">
                  {m.name ?? m.email} <span className="font-normal capitalize text-slate-500">· {m.role.replace(/_/g, ' ')}</span>
                  {!m.active && <span className="ml-1 text-slate-500">(disabled)</span>}
                </p>
                <p className="text-slate-600">{describeAccess(m, members)}</p>
              </div>
              {m.active && editing !== m.user_id && (
                <button type="button" onClick={() => { setEditing(m.user_id); setSaved(''); }}
                  className="rounded-lg border px-3 py-1.5 text-sm" aria-label={`Edit access for ${m.name ?? m.email}`}>
                  Edit
                </button>
              )}
            </div>
            {editing === m.user_id && data && (
              <PersonEditor member={m} members={members} departments={data.departments}
                onCancel={() => setEditing(null)}
                onSaved={() => { setEditing(null); setSaved(`Saved access for ${m.name ?? m.email}.`); load(); }} />
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
