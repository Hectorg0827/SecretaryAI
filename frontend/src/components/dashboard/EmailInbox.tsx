/**
 * Priority Inbox — the hero panel of the dashboard.
 *
 * Each email row shows:
 *   · Priority badge (HIGH / MED / LOW)  colored left border
 *   · Sender + subject + AI snippet
 *   · "Draft Reply" quick-action button
 *
 * Clicking a row expands it to show:
 *   · Full AI summary
 *   · AI-drafted reply (loaded on demand)
 *   · Mark read / Snooze actions
 */
import React, { useEffect, useState } from 'react';
import clsx from 'clsx';
import {
  ChevronDown, ChevronRight, Loader2, Mail, MailOpen,
  Sparkles, Send, X, RefreshCw,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api, PriorityEmail } from '../../lib/api';
import { Badge } from '../ui/Badge';
import { Card, CardHeader } from '../ui/Card';
import { SkeletonCard } from '../ui/Skeleton';

// ─── Priority config ──────────────────────────────────────────────────────────

const PRIORITY_BORDER: Record<string, string> = {
  high:   'border-l-red-400',
  medium: 'border-l-amber-400',
  low:    'border-l-slate-200',
};

// ─── Email row ────────────────────────────────────────────────────────────────

interface EmailRowProps {
  email: PriorityEmail;
  onMarkRead: (id: string) => void;
}

function EmailRow({ email, onMarkRead }: EmailRowProps) {
  const [open,        setOpen]        = useState(false);
  const [draft,       setDraft]       = useState<string | null>(null);
  const [draftSubj,   setDraftSubj]   = useState('');
  const [loadingDraft, setLoadingDraft] = useState(false);
  const [sending,     setSending]     = useState(false);
  const [showDraft,   setShowDraft]   = useState(false);

  const relativeTime = (iso: string) => {
    const diff = Date.now() - new Date(iso).getTime();
    const m = Math.floor(diff / 60_000);
    if (m < 60)  return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24)  return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  };

  const loadDraft = async () => {
    if (draft !== null) { setShowDraft(true); return; }
    setLoadingDraft(true);
    try {
      const res = await api.emails.draftReply(email.id);
      setDraft(res.draft);
      setDraftSubj(res.subject);
      setShowDraft(true);
    } catch {
      toast.error('Could not generate draft reply');
    } finally {
      setLoadingDraft(false);
    }
  };

  const sendReply = async () => {
    if (!draft) return;
    setSending(true);
    try {
      await api.emails.sendReply(email.id, draft, draftSubj, email.from_email);
      toast.success('Reply sent');
      onMarkRead(email.id);
      setShowDraft(false);
    } catch {
      toast.error('Failed to send reply');
    } finally {
      setSending(false);
    }
  };

  const markRead = async () => {
    await api.emails.markRead(email.id).catch(() => null);
    onMarkRead(email.id);
  };

  return (
    <div
      className={clsx(
        'border-l-4 transition-all duration-200',
        PRIORITY_BORDER[email.ai_priority],
        !email.is_read && 'bg-white',
        email.is_read  && 'bg-slate-50 opacity-70',
      )}
    >
      {/* Collapsed header row */}
      <button
        onClick={() => setOpen(!open)}
        className="w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-slate-50 transition-colors"
      >
        <div className="mt-0.5 text-slate-300 flex-shrink-0">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge variant={email.ai_priority} />
            <span className={clsx('text-sm font-medium text-slate-800 truncate max-w-[160px]', !email.is_read && 'font-semibold')}>
              {email.from}
            </span>
            <span className="text-xs text-slate-400 ml-auto flex-shrink-0">
              {relativeTime(email.received_at)}
            </span>
          </div>
          <div className="text-sm text-slate-600 truncate mt-0.5">{email.subject}</div>
          <div className="text-xs text-slate-400 truncate mt-0.5">{email.ai_summary || email.snippet}</div>
        </div>

        {/* Quick draft button — stops propagation so it doesn't toggle expand */}
        <button
          onClick={(e) => { e.stopPropagation(); setOpen(true); loadDraft(); }}
          className="flex-shrink-0 flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-700 border border-blue-200 hover:border-blue-300 rounded-lg px-2.5 py-1 bg-blue-50 hover:bg-blue-100 transition-colors whitespace-nowrap"
        >
          <Sparkles size={11} />
          Draft reply
        </button>
      </button>

      {/* Expanded body */}
      {open && (
        <div className="px-5 pb-4 animate-slide-down">
          {/* AI Summary block */}
          <div className="bg-slate-50 border border-slate-200 rounded-lg p-3 mb-3">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-500 mb-1.5">
              <Sparkles size={11} className="text-blue-500" />
              AI Summary
            </div>
            <p className="text-sm text-slate-700 leading-relaxed">{email.ai_summary}</p>
            {email.ai_action_needed && (
              <div className="mt-2 text-xs font-medium text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1">
                Action needed: {email.ai_action_needed}
              </div>
            )}
          </div>

          {/* Draft reply area */}
          {showDraft && draft !== null && (
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-3 animate-fade-in">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-blue-700 flex items-center gap-1">
                  <Sparkles size={11} />
                  Smart Reply Draft
                </span>
                <button onClick={() => setShowDraft(false)} className="text-slate-400 hover:text-slate-600">
                  <X size={13} />
                </button>
              </div>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                className="w-full text-sm text-slate-700 bg-white border border-blue-200 rounded p-2.5 resize-none focus:outline-none focus:ring-2 focus:ring-blue-400"
                rows={6}
              />
              <div className="flex items-center gap-2 mt-2">
                <button
                  onClick={sendReply}
                  disabled={sending}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors disabled:opacity-50"
                >
                  {sending ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
                  {sending ? 'Sending…' : 'Send Reply'}
                </button>
                <button
                  onClick={() => setShowDraft(false)}
                  className="px-3 py-1.5 text-sm text-slate-500 hover:text-slate-700 hover:bg-slate-100 rounded-lg transition-colors"
                >
                  Discard
                </button>
              </div>
            </div>
          )}

          {/* Actions bar */}
          <div className="flex items-center gap-2">
            <button
              onClick={loadDraft}
              disabled={loadingDraft}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 border border-blue-200 rounded-lg transition-colors disabled:opacity-50"
            >
              {loadingDraft
                ? <Loader2 size={12} className="animate-spin" />
                : <Sparkles size={12} />
              }
              {showDraft ? 'Regenerate' : 'Draft Smart Reply'}
            </button>

            {!email.is_read && (
              <button
                onClick={markRead}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-500 hover:text-slate-700 bg-slate-100 hover:bg-slate-200 rounded-lg transition-colors"
              >
                <MailOpen size={12} />
                Mark read
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function EmailInbox() {
  const [emails,  setEmails]  = useState<PriorityEmail[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter,  setFilter]  = useState<'all' | 'unread'>('unread');

  const fetchEmails = async () => {
    try {
      const res = await api.dashboard.emails();
      setEmails(res.emails ?? []);
    } catch {
      // API endpoint may not exist yet — use empty state gracefully
      setEmails([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchEmails(); }, []);

  const handleMarkRead = (id: string) => {
    setEmails((prev) => prev.map((e) => e.id === id ? { ...e, is_read: true } : e));
  };

  const visible = filter === 'unread'
    ? emails.filter((e) => !e.is_read)
    : emails;

  const unreadCount = emails.filter((e) => !e.is_read).length;

  if (loading) return <SkeletonCard lines={4} />;

  return (
    <Card>
      <CardHeader
        title="Priority Inbox"
        count={unreadCount > 0 ? `${unreadCount} unread` : 'All read'}
        action={
          <div className="flex items-center gap-1">
            <button
              onClick={fetchEmails}
              className="p-1 rounded hover:bg-slate-100 transition-colors"
              title="Refresh"
            >
              <RefreshCw size={13} />
            </button>
            <div className="flex rounded-lg border border-slate-200 overflow-hidden text-xs">
              {(['unread', 'all'] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={clsx(
                    'px-2.5 py-1 font-medium capitalize transition-colors',
                    filter === f ? 'bg-slate-800 text-white' : 'text-slate-500 hover:bg-slate-50',
                  )}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>
        }
      />

      {visible.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 text-slate-400">
          <Mail size={28} className="mb-2 opacity-40" />
          <p className="text-sm">
            {filter === 'unread' ? 'No unread emails — inbox is clear.' : 'No emails found.'}
          </p>
        </div>
      ) : (
        <div className="divide-y divide-slate-100">
          {visible.map((email) => (
            <EmailRow key={email.id} email={email} onMarkRead={handleMarkRead} />
          ))}
        </div>
      )}
    </Card>
  );
}
