/**
 * Team management (owner only): invite people, change their role, remove or
 * restore their access. Every rule is enforced by the server; this screen
 * shows what the server answers, including the reason when it says no.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, UserPlus, Copy } from 'lucide-react';
import toast from 'react-hot-toast';
import { api, InviteResult, TeamMember } from '../../lib/api';
import { saveSession } from '../../lib/session';

const ROLE_LABELS: Record<string, { label: string; help: string }> = {
  owner:       { label: 'Owner',       help: 'Everything, including billing, team and connections' },
  manager:     { label: 'Manager',     help: 'Runs day-to-day work and approves actions' },
  sales_rep:   { label: 'Sales rep',   help: 'Accounts, inventory and chat' },
  back_office: { label: 'Back office', help: 'Inventory, orders and reports' },
  viewer:      { label: 'View only',   help: 'Dashboard only' },
};

function message(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

export function TeamSection() {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState('viewer');
  const [inviting, setInviting] = useState(false);
  const [manualLink, setManualLink] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.users.list();
      setMembers(data.users);
    } catch (err) {
      toast.error(message(err, 'Could not load your team'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const afterInvite = (result: InviteResult, address: string) => {
    if (result.email_sent) {
      toast.success(`Invitation sent to ${address}`);
      setManualLink(null);
    } else if (result.invite_link) {
      // Email is not set up on the server: the owner passes the link on.
      setManualLink(result.invite_link);
      toast('The email could not be sent. Copy the link below and give it to them.', { duration: 8000 });
    }
  };

  const invite = async (e: React.FormEvent) => {
    e.preventDefault();
    setInviting(true);
    try {
      const address = email.trim().toLowerCase();
      afterInvite(await api.users.invite(address, role, name.trim()), address);
      setEmail(''); setName(''); setRole('viewer');
      await load();
    } catch (err) {
      toast.error(message(err, 'Could not send the invitation'));
    } finally {
      setInviting(false);
    }
  };

  const change = async (member: TeamMember, changes: { role?: string; is_active?: boolean }, done: string) => {
    setBusyId(member.id);
    try {
      await api.users.update(member.id, changes);
      toast.success(done);
      await load();
    } catch (err) {
      toast.error(message(err, 'That change could not be made'));
    } finally {
      setBusyId(null);
    }
  };

  const resend = async (member: TeamMember) => {
    setBusyId(member.id);
    try {
      afterInvite(await api.users.resendInvite(member.id), member.email);
    } catch (err) {
      toast.error(message(err, 'Could not resend the invitation'));
    } finally {
      setBusyId(null);
    }
  };

  const remove = (member: TeamMember) => {
    if (!window.confirm(`Remove ${member.name || member.email}'s access? They will be signed out immediately. You can restore it later.`)) return;
    change(member, { is_active: false }, 'Access removed');
  };

  return (
    <div className="space-y-5">
      <div className="bg-white rounded-xl border border-slate-100 shadow-sm p-5">
        <h2 className="text-sm font-semibold text-slate-800 mb-1">Invite someone</h2>
        <p className="text-xs text-slate-400 mb-4">They get an email with a link to choose their own password. The link works for 7 days.</p>
        <form onSubmit={invite} className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label htmlFor="invite-email" className="block text-xs font-semibold text-slate-600 mb-1">Email</label>
            <input id="invite-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
              className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400" />
          </div>
          <div>
            <label htmlFor="invite-name" className="block text-xs font-semibold text-slate-600 mb-1">Name (optional)</label>
            <input id="invite-name" type="text" value={name} onChange={(e) => setName(e.target.value)}
              className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400" />
          </div>
          <div>
            <label htmlFor="invite-role" className="block text-xs font-semibold text-slate-600 mb-1">Role</label>
            <select id="invite-role" value={role} onChange={(e) => setRole(e.target.value)}
              className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-400">
              {Object.entries(ROLE_LABELS).map(([value, r]) => <option key={value} value={value}>{r.label}</option>)}
            </select>
            <p className="text-xs text-slate-400 mt-1">{ROLE_LABELS[role]?.help}</p>
          </div>
          <div className="flex items-end">
            <button type="submit" disabled={inviting || !email.includes('@')}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-50">
              {inviting ? <Loader2 size={14} className="animate-spin" /> : <UserPlus size={14} />}
              Send invitation
            </button>
          </div>
        </form>
        {manualLink && (
          <div className="mt-4 bg-amber-50 border border-amber-200 rounded-lg p-3">
            <p className="text-xs text-amber-800 mb-2">Email is not set up on this server, so the invitation was not sent. Give this link to the person yourself — it lets them choose a password, so only share it with them.</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs bg-white border border-amber-200 rounded px-2 py-1.5 break-all">{manualLink}</code>
              <button type="button" onClick={() => { navigator.clipboard.writeText(manualLink); toast.success('Link copied'); }}
                className="flex items-center gap-1 text-xs font-medium text-amber-800 border border-amber-300 rounded-lg px-2.5 py-1.5 bg-white hover:bg-amber-100">
                <Copy size={11} /> Copy
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-100 shadow-sm px-5">
        {loading ? (
          <p className="flex items-center gap-2 text-sm text-slate-400 py-5"><Loader2 size={14} className="animate-spin" /> Loading your team…</p>
        ) : members.map((m) => (
          <div key={m.id} className="flex flex-wrap items-center gap-3 py-4 border-b border-slate-100 last:border-0">
            <div className="flex-1 min-w-[12rem]">
              <div className="text-sm font-medium text-slate-800">{m.name || m.email}</div>
              <div className="text-xs text-slate-400">
                {m.email}
                {m.pending_invite && m.is_active && ' · invitation not accepted yet'}
                {!m.is_active && ' · access removed'}
                {m.two_factor && ' · two-step sign-in on'}
              </div>
            </div>
            <label className="sr-only" htmlFor={`role-${m.id}`}>Role for {m.email}</label>
            <select id={`role-${m.id}`} value={m.role} disabled={busyId === m.id || !m.is_active}
              onChange={(e) => change(m, { role: e.target.value }, 'Role changed')}
              className="text-sm border border-slate-200 rounded-lg px-2.5 py-1.5 bg-white disabled:opacity-50">
              {Object.entries(ROLE_LABELS).map(([value, r]) => <option key={value} value={value}>{r.label}</option>)}
            </select>
            {busyId === m.id ? <Loader2 size={14} className="animate-spin text-slate-400" /> : (
              <>
                {m.pending_invite && m.is_active && (
                  <button type="button" onClick={() => resend(m)}
                    className="text-xs text-blue-600 font-medium border border-blue-200 rounded-lg px-2.5 py-1.5 bg-blue-50 hover:bg-blue-100">
                    Resend invite
                  </button>
                )}
                {m.is_active ? (
                  <button type="button" onClick={() => remove(m)}
                    className="text-xs text-red-500 font-medium border border-red-100 rounded-lg px-2.5 py-1.5 bg-red-50 hover:bg-red-100">
                    Remove access
                  </button>
                ) : (
                  <button type="button" onClick={() => change(m, { is_active: true }, 'Access restored')}
                    className="text-xs text-slate-600 font-medium border border-slate-200 rounded-lg px-2.5 py-1.5 bg-slate-50 hover:bg-slate-100">
                    Restore access
                  </button>
                )}
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Change your own password (signs out your other devices). */
export function ChangePasswordSection() {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (next.length < 8 || !/[A-Za-z]/.test(next) || !/[0-9]/.test(next)) {
      toast.error('Use at least 8 characters, with a letter and a number.'); return;
    }
    if (next !== confirm) { toast.error('The two new passwords do not match.'); return; }
    setSaving(true);
    try {
      const session = await api.auth.changePassword(current, next);
      // The server ended every older session; keep this device signed in.
      saveSession(session);
      setCurrent(''); setNext(''); setConfirm('');
      toast.success('Password changed. Your other devices were signed out.');
    } catch (err) {
      toast.error(message(err, 'Could not change your password'));
    } finally {
      setSaving(false);
    }
  };

  const field = 'w-full text-sm border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400';
  return (
    <form onSubmit={submit} className="bg-white rounded-xl border border-slate-100 shadow-sm p-5 mt-5 space-y-3 max-w-md">
      <h2 className="text-sm font-semibold text-slate-800">Change password</h2>
      <div>
        <label htmlFor="cp-current" className="block text-xs font-semibold text-slate-600 mb-1">Current password</label>
        <input id="cp-current" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} className={field} />
      </div>
      <div>
        <label htmlFor="cp-new" className="block text-xs font-semibold text-slate-600 mb-1">New password</label>
        <input id="cp-new" type="password" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} className={field} />
      </div>
      <div>
        <label htmlFor="cp-confirm" className="block text-xs font-semibold text-slate-600 mb-1">Type the new password again</label>
        <input id="cp-confirm" type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} className={field} />
      </div>
      <button type="submit" disabled={saving || !current || !next || !confirm}
        className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-50">
        {saving && <Loader2 size={14} className="animate-spin" />}
        Change password
      </button>
    </form>
  );
}
