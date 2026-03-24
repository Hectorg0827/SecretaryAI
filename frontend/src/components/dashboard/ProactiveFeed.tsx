import React, { useEffect, useState } from 'react';
import { api, FeedEvent } from '../../lib/api';

const PRIORITY_COLORS = {
  high:   'border-l-red-400 bg-red-50',
  medium: 'border-l-amber-400 bg-amber-50',
  low:    'border-l-slate-300 bg-slate-50',
};

const PRIORITY_DOT = {
  high:   'bg-red-400',
  medium: 'bg-amber-400',
  low:    'bg-slate-300',
};

export function ProactiveFeed() {
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = () => {
    api.feed.list()
      .then(r => setEvents(r.events))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(reload, []);

  const dismiss = (id: string) => {
    api.feed.dismiss(id).then(() => setEvents(ev => ev.filter(e => e.id !== id)));
  };

  const markRead = (id: string) => {
    api.feed.read(id).then(() => setEvents(ev => ev.map(e => e.id === id ? {...e, is_read: true} : e)));
  };

  if (loading) return <div className="h-32 bg-white rounded-xl border border-slate-100 animate-pulse" />;
  if (!events.length) return (
    <div className="bg-white rounded-xl border border-slate-100 p-4 text-sm text-slate-400 text-center">
      No new insights
    </div>
  );

  return (
    <div className="bg-white rounded-xl border border-slate-100 overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
        <span className="text-sm font-semibold text-slate-700">Insights</span>
        <span className="text-xs text-slate-400">{events.filter(e => !e.is_read).length} new</span>
      </div>
      <div className="divide-y divide-slate-100 max-h-80 overflow-y-auto">
        {events.map(ev => (
          <div
            key={ev.id}
            className={`px-4 py-3 border-l-4 ${PRIORITY_COLORS[ev.priority]} ${!ev.is_read ? 'font-medium' : ''}`}
            onClick={() => markRead(ev.id)}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${PRIORITY_DOT[ev.priority]}`} />
                  <p className="text-sm text-slate-800 truncate">{ev.title}</p>
                </div>
                <p className="text-xs text-slate-500 pl-3.5">{ev.body}</p>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); dismiss(ev.id); }}
                className="text-slate-300 hover:text-slate-500 text-xs flex-shrink-0"
              >
                ✕
              </button>
            </div>
            {ev.action_label && (
              <div className="pl-3.5 mt-1.5">
                <button className="text-xs text-blue-600 hover:underline font-medium">
                  {ev.action_label} &rarr;
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
