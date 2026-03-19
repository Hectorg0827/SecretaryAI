import React, { useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import { Plus, Check, StickyNote, Loader2, X, CalendarDays } from 'lucide-react';
import toast from 'react-hot-toast';
import { api, FollowUpNote } from '../../lib/api';
import { Card, CardHeader } from '../ui/Card';

function NoteRow({
  note,
  onDone,
}: {
  note: FollowUpNote;
  onDone: (id: string) => void;
}) {
  const [marking, setMarking] = useState(false);
  const overdue = note.due_date && !note.done && new Date(note.due_date) < new Date();

  const markDone = async () => {
    setMarking(true);
    try {
      await api.dashboard.doneNote(note.id);
      onDone(note.id);
    } catch { toast.error('Could not update note'); }
    finally   { setMarking(false); }
  };

  return (
    <div className={clsx(
      'flex items-start gap-3 px-5 py-3 transition-opacity',
      note.done && 'opacity-40',
    )}>
      <button
        onClick={markDone}
        disabled={note.done || marking}
        className={clsx(
          'mt-0.5 w-4 h-4 flex-shrink-0 rounded border-2 flex items-center justify-center transition-colors',
          note.done
            ? 'bg-emerald-500 border-emerald-500'
            : 'border-slate-300 hover:border-blue-400',
        )}
      >
        {marking ? <Loader2 size={10} className="animate-spin text-slate-400" /> : note.done && <Check size={10} className="text-white" />}
      </button>

      <div className="flex-1 min-w-0">
        <p className={clsx('text-sm text-slate-700', note.done && 'line-through')}>{note.text}</p>
        <div className="flex items-center gap-2 mt-0.5 flex-wrap">
          {note.account_name && (
            <span className="text-xs text-blue-600 bg-blue-50 border border-blue-100 px-1.5 py-0.5 rounded">
              {note.account_name}
            </span>
          )}
          {note.due_date && (
            <span className={clsx(
              'text-xs flex items-center gap-0.5',
              overdue ? 'text-red-500 font-medium' : 'text-slate-400',
            )}>
              <CalendarDays size={10} />
              {new Date(note.due_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
              {overdue && ' · Overdue'}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export function FollowUpNotes() {
  const [notes,   setNotes]   = useState<FollowUpNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [text,    setText]    = useState('');
  const [acct,    setAcct]    = useState('');
  const [due,     setDue]     = useState('');
  const [adding,  setAdding]  = useState(false);
  const [showForm,setShowForm]= useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.dashboard.notes()
      .then((r) => setNotes(r.notes ?? []))
      .catch(() => setNotes([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (showForm) inputRef.current?.focus();
  }, [showForm]);

  const addNote = async () => {
    if (!text.trim()) return;
    setAdding(true);
    try {
      const note = await api.dashboard.addNote(text.trim(), acct || undefined, due || undefined);
      setNotes((prev) => [note, ...prev]);
      setText(''); setAcct(''); setDue('');
      setShowForm(false);
    } catch { toast.error('Could not save note'); }
    finally   { setAdding(false); }
  };

  const markDone = (id: string) => {
    setNotes((prev) => prev.map((n) => n.id === id ? { ...n, done: true } : n));
  };

  const pending = notes.filter((n) => !n.done);
  const done    = notes.filter((n) =>  n.done);

  return (
    <Card>
      <CardHeader
        title="Follow-up Notes"
        count={pending.length > 0 ? pending.length : undefined}
        action={
          <button
            onClick={() => setShowForm(!showForm)}
            className="flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-700"
          >
            {showForm ? <X size={13} /> : <Plus size={13} />}
            {showForm ? 'Cancel' : 'Add note'}
          </button>
        }
      />

      {/* Add note form */}
      {showForm && (
        <div className="px-5 py-4 border-b border-slate-100 bg-slate-50 animate-slide-down space-y-2">
          <input
            ref={inputRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addNote()}
            placeholder="Note text…"
            className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white"
          />
          <div className="flex gap-2">
            <input
              value={acct}
              onChange={(e) => setAcct(e.target.value)}
              placeholder="Account (optional)"
              className="flex-1 text-xs border border-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white"
            />
            <input
              type="date"
              value={due}
              onChange={(e) => setDue(e.target.value)}
              className="text-xs border border-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white"
            />
          </div>
          <button
            onClick={addNote}
            disabled={!text.trim() || adding}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors disabled:opacity-50"
          >
            {adding ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />}
            Save note
          </button>
        </div>
      )}

      {loading ? (
        <div className="px-5 py-4 text-sm text-slate-400">Loading…</div>
      ) : notes.length === 0 ? (
        <div className="flex flex-col items-center py-8 text-slate-400">
          <StickyNote size={24} className="mb-2 opacity-40" />
          <p className="text-sm">No follow-up notes yet.</p>
        </div>
      ) : (
        <div className="divide-y divide-slate-50">
          {pending.map((n) => <NoteRow key={n.id} note={n} onDone={markDone} />)}
          {done.length > 0 && (
            <>
              <div className="px-5 py-2 text-xs font-medium text-slate-400 bg-slate-50">Completed</div>
              {done.slice(0, 3).map((n) => <NoteRow key={n.id} note={n} onDone={markDone} />)}
            </>
          )}
        </div>
      )}
    </Card>
  );
}
