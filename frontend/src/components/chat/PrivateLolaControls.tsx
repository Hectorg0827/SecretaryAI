import React, { useEffect, useState } from 'react';
import { LockKeyhole, Mail, RefreshCw, ShieldCheck } from 'lucide-react';
import { api } from '../../lib/api';
import { PrivateActivityLog } from './PrivateActivityLog';

type Contact = {
  id: string;
  display_name: string;
  email?: string | null;
  phone?: string | null;
  identity_verified: boolean;
};
type Preferences = {
  proactive_enabled: boolean;
  push_enabled: boolean;
  timezone: string;
  reminder_hour: number;
};
const defaults: Preferences = {
  proactive_enabled: false, push_enabled: false,
  timezone: 'America/New_York', reminder_hour: 9,
};

export function PrivateLolaControls({ onUpdated }: { onUpdated: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [prefs, setPrefs] = useState<Preferences>(defaults);
  const [gmailConnected, setGmailConnected] = useState(false);
  const [whatsapp, setWhatsapp] = useState<{ status: string; employee_consented: boolean;
    history_sync_requested: boolean; can_launch_embedded_signup: boolean } | null>(null);
  const [historyConsent, setHistoryConsent] = useState(false);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [person, setPerson] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');

  useEffect(() => {
    if (!expanded) return;
    let active = true;
    setReady(false);
    setError('');
    Promise.all([
      api.get<{ contacts: Contact[] }>('/api/lola/continuity/contacts'),
      api.get<{ preferences: Preferences }>('/api/lola/continuity/preferences'),
      api.settings.integrations(),
      api.get<{ status: string; employee_consented: boolean; history_sync_requested: boolean;
        can_launch_embedded_signup: boolean }>('/api/lola/continuity/whatsapp/readiness'),
    ]).then(([people, settings, integrations, whatsappState]) => {
      if (!active) return;
      setContacts(people.contacts);
      setPrefs(settings.preferences);
      setGmailConnected(Boolean(integrations.gmail?.connected));
      setWhatsapp(whatsappState);
      setHistoryConsent(whatsappState.history_sync_requested);
      setReady(true);
    }).catch(() => {
      if (active) setError('Your private vault is unavailable; no messages were accessed.');
    });
    return () => { active = false; };
  }, [expanded]);

  const saveContact = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!person.trim() || (!email.trim() && !phone.trim())) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const response = await api.post<{ contact: Contact }>('/api/lola/continuity/contacts', {
        display_name: person.trim(), email: email.trim() || null, phone: phone.trim() || null,
      });
      if (!response.contact?.id) throw new Error('Could not store the contact');
      setContacts((rows) => [...rows, response.contact]);
      setPerson(''); setEmail(''); setPhone('');
      setNotice('Contact saved. Confirm its identifiers before Lola links conversations.');
      onUpdated();
    } catch { setError('Could not save private contact.'); }
    finally { setBusy(false); }
  };

  const confirmContact = async (contact: Contact) => {
    setBusy(true); setError('');
    try {
      await api.post('/api/lola/continuity/contacts/' + encodeURIComponent(contact.id) + '/confirm', {});
      setContacts((rows) => rows.map((row) =>
        row.id === contact.id ? { ...row, identity_verified: true } : row
      ));
      setNotice('Identifiers confirmed by you for your private Lola only.');
      onUpdated();
    } catch { setError('Could not confirm the identity.'); }
    finally { setBusy(false); }
  };

  const updatePrefs = async (next: Preferences) => {
    setBusy(true); setError('');
    try {
      await api.put('/api/lola/continuity/preferences', next);
      setPrefs(next);
      setNotice('Private preferences saved.');
      onUpdated();
    } catch { setError('Could not save private preferences.'); }
    finally { setBusy(false); }
  };

  const connectGmail = async () => {
    setBusy(true); setError('');
    try {
      const response = await api.settings.gmailConnectUrl();
      if (!response.url.startsWith('https://accounts.google.com/')) throw Error('Bad OAuth host');
      window.location.assign(response.url);
    } catch {
      setError('Could not start employee-owned Gmail authorization.');
      setBusy(false);
    }
  };

  const updateWhatsAppConsent = async (consent: boolean) => {
    setBusy(true); setError(''); setNotice('');
    try {
      if (consent) {
        const response = await api.put<{ status: string; employee_consented: boolean;
          history_sync_requested: boolean }>('/api/lola/continuity/whatsapp/consent', {
          consent: true, allow_one_time_history_sync: historyConsent,
        });
        setWhatsapp((previous) => previous ? { ...previous,
          status: response.status, employee_consented: true,
          history_sync_requested: response.history_sync_requested,
        } : null);
        setNotice('Your choice was saved privately. WhatsApp is NOT connected or syncing; Meta approval is still required.');
      } else {
        await api.delete('/api/lola/continuity/whatsapp/consent');
        setWhatsapp((previous) => previous ? { ...previous,
          status: 'disconnected', employee_consented: false, history_sync_requested: false,
        } : null);
        setHistoryConsent(false);
        setNotice('Your local WhatsApp connection request was withdrawn.');
      }
      onUpdated();
    } catch {
      setError('Unable to update your private WhatsApp consent.');
    } finally {
      setBusy(false);
    }
  };

  const syncGmail = async () => {
    setBusy(true); setError(''); setNotice('');
    try {
      const r = await api.post<{
        scanned: number; matched_verified_contacts: number; recorded: number;
      }>('/api/lola/continuity/sync/gmail', {});
      setNotice(
        'My Gmail: ' + r.scanned + ' messages scanned; ' +
        r.matched_verified_contacts + ' matched to confirmed contacts; ' +
        r.recorded + ' private events recorded. Nothing shared with the company.'
      );
      onUpdated();
    } catch { setError('Private Gmail synchronization failed; no company mailbox was used.'); }
    finally { setBusy(false); }
  };

  return (
    <section className="mx-4 mt-3 rounded-xl border border-slate-200 bg-white shadow-sm">
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}
        className="flex w-full items-center justify-between gap-3 rounded-xl px-4 py-3 text-left hover:bg-slate-50">
        <span className="flex items-center gap-2 text-sm font-semibold text-slate-800">
          <LockKeyhole size={16} aria-hidden="true" /> My private Lola
        </span>
        <span className="text-xs text-slate-500">{expanded ? 'Close' : 'Connections · Contacts · Privacy'}</span>
      </button>
      {expanded && (
        <div className="max-h-[55vh] space-y-4 overflow-y-auto border-t border-slate-100 p-4">
          <p className="flex items-center gap-2 text-xs text-slate-600">
            <ShieldCheck size={14} aria-hidden="true" />
            Employee-owned data; not a management inbox.
          </p>
          {!ready && !error && <p role="status" className="text-xs text-slate-500">Loading private settings…</p>}
          {error && <p role="alert" className="rounded-lg bg-red-50 p-2 text-xs text-red-700">{error}</p>}
          {notice && <p role="status" className="rounded-lg bg-slate-50 p-2 text-xs text-slate-700">{notice}</p>}
          {ready && (
            <>
              <div className="space-y-2 rounded-lg border border-slate-200 p-3">
                <h3 className="text-sm font-semibold text-slate-800">My Gmail</h3>
                <p className="text-xs text-slate-600">
                  {gmailConnected ? 'Connected to your private employee profile.' : 'No private Gmail connection.'}
                </p>
                <button type="button" disabled={busy} onClick={() => void (gmailConnected ? syncGmail() : connectGmail())}
                  className="rounded-lg bg-slate-800 px-3 py-2 text-xs font-medium text-white disabled:opacity-50">
                  {gmailConnected ? <RefreshCw size={12} className="mr-1 inline" /> :
                    <Mail size={12} className="mr-1 inline" />}
                  {gmailConnected ? ' Sync my recent Gmail' : ' Connect my Gmail'}
                </button>
                <p className="text-xs text-slate-500">
                  Sync begins only on your request. Only confirmed contacts are linked.
                </p>
              </div>
              <div className="space-y-2 rounded-lg border border-slate-200 p-3">
                <h3 className="text-sm font-semibold text-slate-800">My WhatsApp Business</h3>
                <p className="text-xs font-medium text-amber-800">
                  {whatsapp?.employee_consented
                    ? 'Permission requested — Meta Coexistence not connected.'
                    : 'Not connected — official Meta Coexistence approval is pending.'}
                </p>
                <p className="text-xs text-slate-600">
                  Lola does not access your WhatsApp account. You can keep using your existing
                  WhatsApp Business app and record an activity manually below.
                  Manual notes are marked as unverified by WhatsApp.
                </p>
                <label className="flex items-center gap-2 text-xs text-slate-600">
                  <input type="checkbox" disabled={busy || Boolean(whatsapp?.employee_consented)}
                    checked={historyConsent}
                    onChange={(event) => setHistoryConsent(event.target.checked)} />
                  Request optional one-time past message history sharing if Meta approves it
                </label>
                <button type="button" disabled={busy}
                  onClick={() => void updateWhatsAppConsent(!whatsapp?.employee_consented)}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-medium disabled:opacity-50">
                  {whatsapp?.employee_consented
                    ? 'Withdraw my connection request'
                    : 'Request a private WhatsApp connection'}
                </button>
                <p className="text-xs text-slate-500">
                  Recording a request does not grant Meta access or sync any chats.
                  Only the owner can grant consent. Managers cannot connect a number on behalf of an employee.
                </p>
                <a
                  className="inline-block text-xs font-medium text-slate-700 underline"
                  href="https://developers.facebook.com/documentation/business-messaging/whatsapp/overview/"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Meta's official WhatsApp Business Platform
                </a>
              </div>
              <div className="space-y-2 rounded-lg border border-slate-200 p-3">
                <h3 className="text-sm font-semibold text-slate-800">My verified contact links</h3>
                <p className="text-xs text-slate-600">
                  Add the email and WhatsApp number of one person; confirm that they belong together.
                </p>
                <form onSubmit={(e) => void saveContact(e)} className="grid gap-2 sm:grid-cols-3">
                  <input aria-label="Contact name" value={person} onChange={(e) => setPerson(e.target.value)}
                    placeholder="Name" maxLength={120} required className="rounded-lg border border-slate-300 p-2 text-xs" />
                  <input aria-label="Contact email" value={email} onChange={(e) => setEmail(e.target.value)}
                    placeholder="Email" type="email" maxLength={254} className="rounded-lg border border-slate-300 p-2 text-xs" />
                  <input aria-label="WhatsApp number" value={phone} onChange={(e) => setPhone(e.target.value)}
                    placeholder="WhatsApp number" type="tel" maxLength={40} className="rounded-lg border border-slate-300 p-2 text-xs" />
                  <button type="submit" disabled={busy || !person.trim() || !(email.trim() || phone.trim())}
                    className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-medium disabled:opacity-50 sm:col-span-3">
                    Save private contact
                  </button>
                </form>
                {contacts.map((contact) => (
                  <div key={contact.id} className="flex items-center justify-between gap-2 border-t border-slate-100 pt-2">
                    <div className="min-w-0">
                      <p className="truncate text-xs font-medium text-slate-800">{contact.display_name}</p>
                      <p className="truncate text-xs text-slate-500">
                        {[contact.email, contact.phone].filter(Boolean).join(' · ')}
                      </p>
                    </div>
                    {contact.identity_verified ? (
                      <span className="text-xs text-emerald-700">Confirmed by you</span>
                    ) : (
                      <button type="button" disabled={busy} onClick={() => void confirmContact(contact)}
                        className="rounded-lg border border-slate-300 px-2 py-1 text-xs disabled:opacity-50">
                        Confirm
                      </button>
                    )}
                  </div>
                ))}
              </div>
              <PrivateActivityLog contacts={contacts} disabled={busy} onUpdated={onUpdated} />
              <div className="space-y-2 rounded-lg border border-slate-200 p-3">
                <h3 className="text-sm font-semibold text-slate-800">Private reminders</h3>
                <label className="flex items-center gap-2 text-xs text-slate-700">
                  <input type="checkbox" checked={prefs.proactive_enabled} disabled={busy}
                    onChange={(e) => void updatePrefs({ ...prefs, proactive_enabled: e.target.checked })} />
                  Allow proactive personal follow-ups
                </label>
                <label className="flex items-center gap-2 text-xs text-slate-700">
                  <input type="checkbox" checked={prefs.push_enabled} disabled={busy}
                    onChange={(e) => void updatePrefs({ ...prefs, push_enabled: e.target.checked })} />
                  Allow generic personal push reminders
                </label>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
