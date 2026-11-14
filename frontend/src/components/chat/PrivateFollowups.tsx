import React, { useEffect, useState } from 'react';
import { LockKeyhole, Mail, MessageCircle, Check } from 'lucide-react';
import { api } from '../../lib/api';

interface PrivateFollowup {
  event_id: string;
  contact_name: string;
  project_key: string;
  last_channel: string;
  due_at: string;
  available_channels: Array<'email' | 'whatsapp'>;
}

type PrivateResponse = { count: number; private: true; followups: PrivateFollowup[] };

export function PrivateFollowups({ onDraft, refreshKey }: {
  onDraft: (request: string) => void;
  refreshKey: number;
}) {
  const [items, setItems] = useState<PrivateFollowup[]>([]);

  useEffect(() => {
    let mounted = true;
    Promise.all([
      api.get<PrivateResponse>('/api/lola/continuity/followups'),
      api.get<{ preferences: { proactive_enabled: boolean } }>('/api/lola/continuity/preferences'),
    ]).then(([result, settings]) => {
      if (mounted) setItems(settings.preferences.proactive_enabled ? result.followups.slice(0, 3) : []);
    })
      // If staging vault migration is absent, keep personal content hidden.
      .catch(() => { if (mounted) setItems([]); });
    return () => { mounted = false; };
  }, [refreshKey]);

  const resolve = async (eventId: string) => {
    try {
      await api.post<{ updated: boolean }>(
        '/api/lola/continuity/followups/' + encodeURIComponent(eventId) + '/resolve', {}
      );
      setItems((existing) => existing.filter((item) => item.event_id !== eventId));
    } catch {
      // Fail closed: don't claim the reminder was dismissed.
    }
  };

  if (!items.length) return null;

  return (
    <section aria-label="Your private Lola follow-ups"
      className="mx-4 mt-3 rounded-xl border border-slate-200 bg-slate-50/90 p-3">
      <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-600">
        <LockKeyhole size={13} aria-hidden="true" />
        Lola's private follow-ups · Only visible to you
      </div>
      {items.map((item) => (
        <div key={item.event_id} className="border-t border-slate-200 py-3 first:border-0 first:pt-1">
          <p className="text-sm font-semibold text-slate-800">{item.contact_name}</p>
          <p className="mb-2 text-xs text-slate-600">
            {item.project_key} · Waiting for feedback after {item.last_channel}
          </p>
          <div className="flex flex-wrap gap-2">
            {item.available_channels.includes('email') && (
              <button type="button" className="rounded-lg border border-slate-300 px-2 py-1 text-xs hover:bg-white"
                onClick={() => onDraft(
                  'Please prepare, but do not send, a follow-up email to ' + item.contact_name
                  + ' about ' + item.project_key + '. Use only my private verified context.'
                )}>
                <Mail size={12} className="mr-1 inline" /> Draft email
              </button>
            )}
            {item.available_channels.includes('whatsapp') && (
              <button type="button" className="rounded-lg border border-slate-300 px-2 py-1 text-xs hover:bg-white"
                onClick={() => onDraft(
                  'Please prepare, but do not send, a WhatsApp follow-up to ' + item.contact_name
                  + ' about ' + item.project_key + '. Use only my private verified context.'
                )}>
                <MessageCircle size={12} className="mr-1 inline" /> Draft WhatsApp
              </button>
            )}
            <button type="button" className="rounded-lg border border-slate-300 px-2 py-1 text-xs hover:bg-white"
              onClick={() => void resolve(item.event_id)}>
              <Check size={12} className="mr-1 inline" /> Done
            </button>
          </div>
        </div>
      ))}
    </section>
  );
}
