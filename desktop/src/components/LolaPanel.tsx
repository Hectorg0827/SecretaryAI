/**
 * "Ask Lola" on the desktop dashboard. Text conversation with the same
 * backend orchestration as the web app. Approvals are deliberately handed off
 * to Lion BI in the system browser so this privileged webview never executes
 * business actions itself.
 */
import React, { useRef, useState } from 'react';
import { lolaTurn, type DesktopLolaEvent } from '../lola/client';

interface Turn {
  id: number;
  q: string;
  text: string;
  status: 'streaming' | 'complete' | 'error' | 'interrupted';
  step?: string;
  notes: string[];
  actions: { id: string; title: string }[];
  error?: string;
}

const GOLD = '#D6A84E';

export default function LolaPanel({ onOpenApp }: { onOpenApp: (path: string) => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const conv = useRef<string | undefined>(undefined);
  const ctrl = useRef<AbortController | null>(null);
  const seq = useRef(0);
  const streaming = turns.some((t) => t.status === 'streaming');

  const patch = (id: number, fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? fn(t) : t)));

  async function ask(q: string) {
    if (!q.trim()) return;
    if (ctrl.current) { ctrl.current.abort(); setTurns((ts) => ts.map((t) => (t.status === 'streaming' ? { ...t, status: 'interrupted' } : t))); }
    const id = ++seq.current;
    const c = new AbortController();
    ctrl.current = c;
    setTurns((ts) => [...ts, { id, q, text: '', status: 'streaming', notes: [], actions: [] }]);
    const lang = /[¿¡ñ]|\b(qué|cómo|facturas|ventas|envío|mis|hoy)\b/i.test(q) ? 'es' : 'en';
    let sawDone = false;
    for await (const ev of lolaTurn(q, conv.current, lang, c.signal) as AsyncGenerator<DesktopLolaEvent>) {
      if (c.signal.aborted) break;
      if (ev.type === 'conversation') conv.current = ev.conversation_id;
      else if (ev.type === 'status') patch(id, (t) => ({ ...t, step: ev.state === 'working' ? ev.step : undefined }));
      else if (ev.type === 'text') patch(id, (t) => ({ ...t, step: undefined, text: t.text + ev.delta }));
      else if (ev.type === 'notice') patch(id, (t) => ({ ...t, notes: [...t.notes, ev.notice.message] }));
      else if (ev.type === 'card' && ev.card.availability !== 'ok') patch(id, (t) => ({ ...t, notes: [...t.notes, `${ev.card.title}: ${ev.card.availability_note ?? 'unavailable'}`] }));
      else if (ev.type === 'action_preview') patch(id, (t) => ({ ...t, actions: [...t.actions, { id: ev.action.id, title: ev.action.title }] }));
      else if (ev.type === 'error') patch(id, (t) => ({ ...t, status: 'error', error: ev.message }));
      else if (ev.type === 'done') sawDone = true;
    }
    if (!c.signal.aborted) patch(id, (t) => (t.status === 'streaming' ? (sawDone ? { ...t, status: 'complete' } : { ...t, status: 'error', error: 'I didn’t get a complete answer. Nothing was executed.' }) : t));
    if (ctrl.current === c) ctrl.current = null;
  }

  const submit = (e: React.FormEvent) => { e.preventDefault(); const q = input; setInput(''); void ask(q); };

  return (
    <section aria-label="Lola" style={s.panel}>
      <div style={s.head}>
        <span aria-hidden style={{ ...s.orb, animation: streaming ? 'lolaPulse 1.6s ease-in-out infinite' : undefined }} />
        <div>
          <div style={{ fontWeight: 600, fontFamily: 'Georgia, serif', fontSize: 16 }}>Lola</div>
          <div aria-live="polite" style={s.sub}>{streaming ? (turns[turns.length - 1]?.step ? `Working: ${turns[turns.length - 1].step}` : 'Thinking…') : 'Your chief of staff'}</div>
        </div>
      </div>
      <div role="log" aria-live="polite" style={s.log}>
        {turns.length === 0 && (
          <div style={s.sub}>
            Ask in plain words — “What’s happening with my business?” or “¿Qué facturas están vencidas?”
          </div>
        )}
        {turns.map((t) => (
          <div key={t.id} style={{ marginBottom: 14 }}>
            <div style={s.q}>{t.q}</div>
            {t.text && <div style={s.a}>{t.text}</div>}
            {t.notes.map((n, i) => <div key={i} role="status" style={s.note}>{n}</div>)}
            {t.actions.map((a) => (
              <div key={a.id} style={s.action}>
                <span>Proposed: {a.title}</span>
                <button type="button" style={s.goldBtn} onClick={() => onOpenApp('/lola')}>Review in Lion BI ↗</button>
              </div>
            ))}
            {t.status === 'interrupted' && <div style={s.sub}>Interrupted — nothing was executed.</div>}
            {t.status === 'error' && (
              <div role="alert" style={s.err}>
                {t.error} <button type="button" style={s.linkBtn} onClick={() => ask(t.q)}>Try again</button>
              </div>
            )}
          </div>
        ))}
      </div>
      <form onSubmit={submit} style={s.form}>
        <label htmlFor="lola-desktop-input" style={s.srOnly}>Ask Lola</label>
        <input id="lola-desktop-input" value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask Lola anything about the business…" style={s.input} />
        {streaming && !input.trim()
          ? <button type="button" style={s.ghost} onClick={() => { ctrl.current?.abort(); setTurns((ts) => ts.map((t) => (t.status === 'streaming' ? { ...t, status: 'interrupted' } : t))); }}>Stop</button>
          : <button type="submit" style={s.goldBtn} disabled={!input.trim()}>Ask</button>}
      </form>
      <style>{'@keyframes lolaPulse{50%{transform:scale(1.08)}} @media (prefers-reduced-motion: reduce){*{animation:none!important}}'}</style>
    </section>
  );
}

const s: Record<string, React.CSSProperties> = {
  panel: { marginTop: 24, background: '#0B1324', border: '1px solid #24304A', borderRadius: 16, maxWidth: 760, display: 'flex', flexDirection: 'column', minHeight: 360 },
  head: { display: 'flex', alignItems: 'center', gap: 12, padding: '14px 16px', borderBottom: '1px solid #24304A' },
  orb: { width: 32, height: 32, borderRadius: '50%', background: 'radial-gradient(circle at 32% 28%, #FFE2A3 0%, #F0C674 20%, transparent 48%), radial-gradient(circle, #1b2a4a 0%, #070b14 100%)', boxShadow: `0 0 0 1px ${GOLD}55`, flexShrink: 0 },
  sub: { color: '#8C97AD', fontSize: 12.5 },
  log: { flex: 1, padding: 16, overflowY: 'auto', maxHeight: 420 },
  q: { marginLeft: 'auto', width: 'fit-content', maxWidth: '85%', background: '#1E2A44', borderRadius: 12, padding: '7px 12px', fontSize: 14, marginBottom: 8 },
  a: { fontSize: 14.5, lineHeight: 1.55, whiteSpace: 'pre-wrap', color: '#E8ECF4' },
  note: { marginTop: 8, fontSize: 13, border: '1px solid #24304A', borderRadius: 10, padding: '8px 10px', color: '#E8ECF4' },
  action: { marginTop: 8, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, fontSize: 13, border: `1px solid ${GOLD}55`, borderRadius: 10, padding: '8px 10px' },
  err: { marginTop: 8, fontSize: 13, border: '1px solid #E07A6B66', borderRadius: 10, padding: '8px 10px' },
  form: { display: 'flex', gap: 8, padding: 12, borderTop: '1px solid #24304A' },
  input: { flex: 1, background: '#101A30', border: '1px solid #24304A', color: '#E8ECF4', borderRadius: 10, padding: '9px 12px', fontSize: 14 },
  goldBtn: { background: GOLD, color: '#070B14', border: 'none', borderRadius: 10, padding: '8px 14px', fontWeight: 600, fontSize: 13, cursor: 'pointer' },
  ghost: { background: 'transparent', border: '1px solid #334155', color: '#cbd5e1', padding: '8px 14px', borderRadius: 10, fontSize: 13, cursor: 'pointer' },
  linkBtn: { background: 'none', border: 'none', color: '#F0C674', textDecoration: 'underline', cursor: 'pointer', fontSize: 13 },
  srOnly: { position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' },
};
