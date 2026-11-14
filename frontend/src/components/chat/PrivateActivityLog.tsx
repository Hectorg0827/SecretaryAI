import React, { useState } from 'react';
import { MessageSquareText } from 'lucide-react';
import { api } from '../../lib/api';

type Contact = {
  id: string;
  display_name: string;
  identity_verified: boolean;
};

type ActivityType = 'draft_sent' | 'quote_sent' | 'feedback' | 'request' | 'closed';

const TYPES: { value: ActivityType; label: string; direction: 'inbound' | 'outbound' }[] = [
  { value: 'draft_sent', label: 'I sent a draft', direction: 'outbound' },
  { value: 'quote_sent', label: 'I sent a quotation', direction: 'outbound' },
  { value: 'feedback', label: 'The customer replied', direction: 'inbound' },
  { value: 'request', label: 'The customer requested something', direction: 'inbound' },
  { value: 'closed', label: 'I closed this follow-up', direction: 'outbound' },
];

export function PrivateActivityLog({
  contacts, disabled, onUpdated,
}: {
  contacts: Contact[];
  disabled: boolean;
  onUpdated: () => void;
}) {
  const [contactId, setContactId] = useState('');
  const [project, setProject] = useState('');
  const [activity, setActivity] = useState<ActivityType>('draft_sent');
  const [summary, setSummary] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');

  const eligible = contacts.filter((contact) => contact.identity_verified);
  const save = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!contactId || !project.trim() || busy) return;
    const chosen = TYPES.find((kind) => kind.value === activity);
    if (!chosen) return;
    setBusy(true);
    setStatus('');
    try {
      await api.post('/api/lola/continuity/events', {
        contact_id: contactId,
        project_key: project.trim(),
        channel: 'whatsapp',
        direction: chosen.direction,
        kind: chosen.value,
        occurred_at: new Date().toISOString(),
        summary: summary.trim(),
        source_ref: null,
      });
      setStatus('Saved to your private Lola as a manual note—not a verified WhatsApp message.');
      setSummary('');
      onUpdated();
    } catch {
      setStatus('Could not save the private note. No message was sent.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 rounded-lg border border-slate-200 p-3">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-800">
        <MessageSquareText size={14} aria-hidden="true" /> Record my WhatsApp activity
      </h3>
      <p className="text-xs text-slate-600">
        Manual notes are not WhatsApp API synchronization. You control what Lola remembers.
        Messages are never sent from this form or shared with management.
      </p>
      {!eligible.length ? (
        <p className="text-xs text-slate-500">Confirm at least one private contact first.</p>
      ) : (
        <form className="grid gap-2" onSubmit={(e) => void save(e)}>
          <select aria-label="Private contact" value={contactId} required
            onChange={(e) => setContactId(e.target.value)}
            className="rounded-lg border border-slate-300 p-2 text-xs">
            <option value="">Choose your contact</option>
            {eligible.map((contact) => (
              <option key={contact.id} value={contact.id}>{contact.display_name}</option>
            ))}
          </select>
          <input aria-label="Project or email subject" value={project} required
            onChange={(e) => setProject(e.target.value)}
            placeholder="Project, e.g. Label Artwork" maxLength={140}
            className="rounded-lg border border-slate-300 p-2 text-xs" />
          <select aria-label="Activity" value={activity}
            onChange={(e) => setActivity(e.target.value as ActivityType)}
            className="rounded-lg border border-slate-300 p-2 text-xs">
            {TYPES.map((kind) => (
              <option key={kind.value} value={kind.value}>{kind.label}</option>
            ))}
          </select>
          <textarea aria-label="Optional private note" value={summary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder="Private context (optional)" maxLength={1000} rows={2}
            className="resize-none rounded-lg border border-slate-300 p-2 text-xs" />
          <button type="submit" disabled={disabled || busy || !contactId || !project.trim()}
            className="rounded-lg bg-slate-800 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">
            {busy ? 'Saving…' : 'Remember this privately'}
          </button>
        </form>
      )}
      {status && <p role="status" className="text-xs text-slate-600">{status}</p>}
    </div>
  );
}
