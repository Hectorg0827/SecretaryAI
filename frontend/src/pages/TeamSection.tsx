import { useEffect, useState } from 'react';
import { api } from '../lib/api';

type Member = { id: string; full_name: string | null; email: string; role: string; is_active: boolean };
const INVITE_ROLES = [
  ['manager', 'Manager'],
  ['finance_controller', 'Finance Controller (AR, AP, ledger)'],
  ['ar_specialist', 'Accounts Receivable (AR only)'],
  ['ap_specialist', 'Accounts Payable (AP only)'],
  ['pricing_billbacks', 'Pricing & Billbacks (pricing only)'],
] as const;
type InviteRole = (typeof INVITE_ROLES)[number][0];

/** Owner-only team management; server always re-checks owner and company. */
export function TeamSection() {
  const [members, setMembers] = useState<Member[]>([]);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<InviteRole>('manager');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [sent, setSent] = useState(false);
  const [notice, setNotice] = useState('');
  const [memberRoles, setMemberRoles] = useState<Record<string, InviteRole>>({});
  const [busyMember, setBusyMember] = useState<string | null>(null);
  const [confirmDisable, setConfirmDisable] = useState<string | null>(null);

  const reloadMembers = async () => {
    const result = await api.get<{ members: Member[] }>('/api/team/members');
    setMembers(result.members);
    setMemberRoles(Object.fromEntries(result.members.map((member) => [
      member.id, INVITE_ROLES.some(([role]) => role === member.role)
        ? member.role as InviteRole : 'manager',
    ])));
  };

  useEffect(() => {
    reloadMembers()
      .catch(() => setError('Could not load team members.'));
  }, []);

  const changeRole = async (member: Member) => {
    const selected = memberRoles[member.id];
    if (!selected || selected === member.role) return;
    setBusyMember(member.id);
    setError('');
    setNotice('');
    try {
      await api.patch(`/api/team/members/${member.id}/role`, { role: selected });
      await reloadMembers();
      setNotice(`Updated ${member.full_name || member.email}'s role. Their old session can no longer access authenticated APIs.`);
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Could not update team role');
    } finally {
      setBusyMember(null);
    }
  };

  const disableMember = async (member: Member) => {
    setBusyMember(member.id);
    setError('');
    setNotice('');
    try {
      await api.delete(`/api/team/members/${member.id}`);
      await reloadMembers();
      setNotice(`Disabled ${member.full_name || member.email}. Their existing session can no longer access authenticated APIs.`);
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Could not disable team member');
    } finally {
      setBusyMember(null);
      setConfirmDisable(null);
    }
  };

  const invite = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    setSent(false);
    setLoading(true);
    try {
      const result = await api.post<{ status: string }>('/api/team/invitations', {
        full_name: name.trim(), email: email.trim().toLowerCase(), role,
      });
      if (result.status !== 'sent') throw new Error('The invitation was not accepted by the mail provider.');
      setSent(true);
      setName('');
      setEmail('');
      setRole('manager');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not send invitation');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-5">
      <p className="text-sm text-slate-600">Invite a Manager or a read-only finance department specialist. Only Managers can approve purchase orders; finance specialists cannot post payments, approve expenses, or change pricing. Invites expire after 48 hours and are limited to the configured staging test mailbox. General outgoing email remains disabled.</p>
      <form onSubmit={invite} className="space-y-3">
        <label className="block text-sm font-medium" htmlFor="team-role">Department role</label>
        <select id="team-role" value={role} onChange={(e) => setRole(e.target.value as InviteRole)}
          className="w-full border rounded-lg px-3 py-2">
          {INVITE_ROLES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <label className="block text-sm font-medium" htmlFor="team-name">Team member name</label>
        <input id="team-name" required minLength={2} maxLength={120} value={name}
          onChange={(e) => setName(e.target.value)} className="w-full border rounded-lg px-3 py-2" />
        <label className="block text-sm font-medium" htmlFor="team-email">Team member email</label>
        <input id="team-email" type="email" required value={email} maxLength={254}
          onChange={(e) => setEmail(e.target.value)} className="w-full border rounded-lg px-3 py-2" />
        <button className="rounded-lg bg-blue-600 text-white font-medium px-4 py-2 disabled:opacity-50"
          disabled={loading || name.trim().length < 2 || !email.includes('@')} type="submit">
          {loading ? 'Sending invitation…' : 'Send staging invitation'}
        </button>
      </form>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {sent && <p role="status" className="text-sm text-emerald-700">Invitation accepted by the email provider. The recipient must open their email and activate their role before gaining any access.</p>}
      <h2 className="font-semibold text-slate-800">Current team members</h2>
      <p className="text-sm text-slate-600">Only the company owner can change staff roles or disable access. Disabled accounts are retained for audit history. You cannot change or disable another owner.</p>
      {notice && <p role="status" className="text-sm text-emerald-700">{notice}</p>}
      <ul className="divide-y divide-slate-100">
        {members.map((member) => (
          <li key={member.id} className="py-3 text-sm flex flex-wrap justify-between items-center gap-3">
            <span>{member.full_name || member.email} <span className="text-slate-500">({member.email})</span></span>
            {member.role === 'owner' || !member.is_active ? (
              <span className="text-slate-600 capitalize">{member.role.replace(/_/g, ' ')}{member.is_active ? ' — protected' : ' — disabled'}</span>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <label className="sr-only" htmlFor={`member-role-${member.id}`}>Role for {member.full_name || member.email}</label>
                <select id={`member-role-${member.id}`} value={memberRoles[member.id] || member.role}
                  onChange={(event) => setMemberRoles((previous) => ({ ...previous, [member.id]: event.target.value as InviteRole }))}
                  className="border rounded-lg px-2 py-1.5" disabled={busyMember !== null}>
                  {INVITE_ROLES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
                <button type="button" onClick={() => changeRole(member)}
                  disabled={busyMember !== null || !memberRoles[member.id] || memberRoles[member.id] === member.role}
                  className="rounded-lg bg-blue-600 text-white px-3 py-1.5 disabled:opacity-50">Save role</button>
                {confirmDisable === member.id ? (
                  <span className="flex gap-2 items-center">
                    <span className="text-red-700">Disable access?</span>
                    <button type="button" onClick={() => disableMember(member)} disabled={busyMember !== null}
                      className="rounded-lg bg-red-700 text-white px-3 py-1.5 disabled:opacity-50">Confirm disable</button>
                    <button type="button" onClick={() => setConfirmDisable(null)} disabled={busyMember !== null}
                      className="rounded-lg border px-3 py-1.5">Cancel</button>
                  </span>
                ) : (
                  <button type="button" onClick={() => setConfirmDisable(member.id)} disabled={busyMember !== null}
                    className="rounded-lg border border-red-300 text-red-700 px-3 py-1.5 disabled:opacity-50">Disable</button>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
