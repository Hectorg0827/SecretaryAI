import { useEffect, useState } from 'react';
import { api } from '../lib/api';

type Member = { id: string; full_name: string | null; email: string; role: string; is_active: boolean };

/** Owner-only team invitation UI. The server independently enforces active owner status. */
export function TeamSection() {
  const [members, setMembers] = useState<Member[]>([]);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [sent, setSent] = useState(false);

  useEffect(() => {
    api.get<{ members: Member[] }>('/api/team/members').then((result) => setMembers(result.members))
      .catch(() => setError('Could not load team members.'));
  }, []);

  const invite = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    setSent(false);
    setLoading(true);
    try {
      const result = await api.post<{ status: string }>('/api/team/invitations', {
        full_name: name.trim(), email: email.trim().toLowerCase(), role: 'manager',
      });
      if (result.status !== 'sent') throw new Error('The invitation was not accepted by the mail provider.');
      setSent(true);
      setName('');
      setEmail('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not send invitation');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-5">
      <p className="text-sm text-slate-600">Invite a Manager who can independently approve purchase orders. Invitations expire after 48 hours. General outgoing email remains disabled in staging.</p>
      <form onSubmit={invite} className="space-y-3">
        <label className="block text-sm font-medium" htmlFor="team-name">Manager name</label>
        <input id="team-name" required minLength={2} maxLength={120} value={name}
          onChange={(e) => setName(e.target.value)} className="w-full border rounded-lg px-3 py-2" />
        <label className="block text-sm font-medium" htmlFor="team-email">Manager email</label>
        <input id="team-email" type="email" required value={email} maxLength={254}
          onChange={(e) => setEmail(e.target.value)} className="w-full border rounded-lg px-3 py-2" />
        <button className="rounded-lg bg-blue-600 text-white font-medium px-4 py-2 disabled:opacity-50"
          disabled={loading || name.trim().length < 2 || !email.includes('@')} type="submit">
          {loading ? 'Sending invitation…' : 'Send Manager invitation'}
        </button>
      </form>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {sent && <p role="status" className="text-sm text-emerald-700">Invitation accepted by the email provider. The recipient must open their email and activate their account before approving anything.</p>}
      <h2 className="font-semibold text-slate-800">Current team members</h2>
      <ul className="divide-y divide-slate-100">
        {members.map((member) => (
          <li key={member.id} className="py-3 text-sm flex justify-between gap-3">
            <span>{member.full_name || member.email} <span className="text-slate-500">({member.email})</span></span>
            <span className="capitalize">{member.role}{member.is_active ? '' : ' — disabled'}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
