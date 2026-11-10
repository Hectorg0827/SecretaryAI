import React, { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { ArrowUp, Mic, MicOff, Paperclip, Square, RotateCw, X, FileText, PauseCircle } from 'lucide-react';
import { getTransport, useLolaStore, type LolaMessage } from '../store';
import { useVoice, stopSpeaking } from '../voice/useVoice';
import { STARTER_PROMPTS, t, greeting, orbStatusLabel } from '../persona';
import { currentRole } from '../../hooks/useAuth';
import type { Attachment, LolaLanguage } from '../contract';
import { EvidenceCardView } from './cards/Cards';
import { ActionCard, NoticeView } from './cards/Actions';
import { Markdownish } from './Markdownish';
import { LolaOrb } from './LolaOrb';
import { useOrbState } from './useOrbState';

const MAX_INLINE_BYTES = 200_000;
const MAX_FILE_BYTES = 15 * 1024 * 1024;
const MAX_EXTRACT_BYTES = 5 * 1024 * 1024;

function LolaReply({ m }: { m: LolaMessage }) {
  const approve = useLolaStore((s) => s.approve);
  const reject = useLolaStore((s) => s.reject);
  const retry = useLolaStore((s) => s.retry);
  const send = useLolaStore((s) => s.send);
  const step = useLolaStore((s) => (s.streamingId === m.id ? s.workingStep : undefined));
  const lang = m.language;
  const streaming = m.status === 'streaming';

  return (
    <article
      className="flex gap-2.5"
      data-testid="lola-reply"
      data-status={m.status}
      aria-busy={streaming}
      aria-label={lang === 'es' ? 'Respuesta de Lola' : 'Lola’s reply'}
    >
      <LolaOrb state={streaming ? (step ? 'working' : 'thinking') : m.status === 'error' ? 'error' : 'idle'} size={26} className="mt-0.5 flex-shrink-0" />
      <div className="min-w-0 flex-1 space-y-2.5">
        {streaming && !m.text && (
          <p className="text-[13px] italic text-lola-mute">{orbStatusLabel(step ? 'working' : 'thinking', lang, step)}</p>
        )}
        {m.text && (
          <div className="space-y-2 text-[14.5px] leading-relaxed text-lola-text">
            <Markdownish text={m.text} />
            {streaming && <span className="lola-caret" aria-hidden />}
          </div>
        )}
        {streaming && m.text && step && <p className="text-[12px] italic text-lola-mute">{orbStatusLabel('working', lang, step)}</p>}
        {m.notices.map((n, i) => <NoticeView key={i} notice={n} lang={lang} />)}
        {m.cards.map((c) => <EvidenceCardView key={c.id} card={c} />)}
        {m.actions.map((a) => (
          <ActionCard
            key={a.id}
            action={a}
            lang={lang}
            receipt={m.receipts.find((r) => r.action_id === a.id)}
            onApprove={() => approve(m.id, a.id)}
            onReject={() => reject(m.id, a.id)}
          />
        ))}
        {m.status === 'interrupted' && (
          <p className="flex items-center gap-1.5 text-[12px] text-lola-mute"><PauseCircle size={13} aria-hidden />{lang === 'es' ? 'Interrumpido — no se ejecutó nada.' : 'Interrupted — nothing was executed.'}</p>
        )}
        {m.status === 'error' && m.error && (
          <div role="alert" className="rounded-xl border border-lola-danger/40 bg-lola-danger/5 px-3 py-2.5 text-[13px] text-lola-text">
            <p>{m.error.message}</p>
            {m.error.retryable && (
              <button type="button" onClick={() => retry(m.id)} className="mt-2 inline-flex min-h-[36px] items-center gap-1.5 rounded-lg border border-lola-line px-3 text-[13px] hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber">
                <RotateCw size={13} aria-hidden />{t('retry', lang)}
              </button>
            )}
          </div>
        )}
        {m.status === 'complete' && m.suggestions.length > 0 && (
          <div className="flex flex-wrap gap-1.5" aria-label={lang === 'es' ? 'Sugerencias' : 'Suggestions'}>
            {m.suggestions.map((s) => (
              <button key={s} type="button" onClick={() => send(s)} className="min-h-[32px] rounded-full border border-lola-line bg-white/[0.02] px-3 text-[12.5px] text-lola-text/90 hover:border-lola-gold/50 hover:text-lola-amber focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber">
                {s}
              </button>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

function UserBubble({ m }: { m: LolaMessage }) {
  return (
    <div className="flex justify-end" data-testid="user-message">
      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-[#1E2A44] px-3.5 py-2 text-[14.5px] text-lola-text">
        {m.channel === 'voice' && <Mic size={11} className="mb-0.5 mr-1 inline text-lola-amber" aria-label="voice" />}
        {m.text}
        {!!m.attachments?.length && (
          <div className="mt-1 flex flex-wrap gap-1">
            {m.attachments.map((a) => <span key={a.name} className="inline-flex items-center gap-1 rounded bg-black/20 px-1.5 py-0.5 text-[11px] text-lola-mute"><FileText size={11} aria-hidden />{a.name}</span>)}
          </div>
        )}
      </div>
    </div>
  );
}

export function EmptyState({ lang, compact = false }: { lang: LolaLanguage; compact?: boolean }) {
  const send = useLolaStore((s) => s.send);
  const prompts = STARTER_PROMPTS[currentRole()] ?? [];
  return (
    <div className={`flex flex-col ${compact ? 'pt-4' : 'items-center pt-10 text-center'}`}>
      {!compact && <LolaOrb state="idle" size={64} className="mb-4" />}
      <h3 className="font-serif text-[22px] text-lola-text">{greeting(lang)} {t('emptyTitle', lang)}</h3>
      <p className="mt-1.5 max-w-md text-[13.5px] text-lola-mute">{t('emptyBody', lang)}</p>
      <div className={`mt-5 grid w-full gap-2 ${compact ? '' : 'max-w-md'}`}>
        {prompts.map((p) => (
          <button key={p.en} type="button" onClick={() => send(p[lang])} className="min-h-[44px] rounded-xl border border-lola-line bg-white/[0.02] px-3.5 text-left text-[13.5px] text-lola-text/90 hover:border-lola-gold/50 hover:bg-lola-gold/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber">
            {p[lang]}
          </button>
        ))}
      </div>
    </div>
  );
}

export function MessageList({ compactEmpty = false }: { compactEmpty?: boolean }) {
  const messages = useLolaStore((s) => s.messages);
  const lang = useLolaStore((s) => s.language);
  const bottom = useRef<HTMLDivElement>(null);
  const last = messages[messages.length - 1];
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: 'end', behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }, [messages.length, last?.text, last?.cards.length, last?.actions.length]);

  return (
    <div
      role="log"
      aria-live="polite"
      aria-relevant="additions"
      aria-label={lang === 'es' ? 'Conversación con Lola' : 'Conversation with Lola'}
      className="lola-scroll flex-1 space-y-5 overflow-y-auto px-4 py-4 sm:px-5"
    >
      {messages.length === 0 && <EmptyState lang={lang} compact={compactEmpty} />}
      {messages.map((m) => (m.role === 'user' ? <UserBubble key={m.id} m={m} /> : <LolaReply key={m.id} m={m} />))}
      <div ref={bottom} />
    </div>
  );
}

export function Composer({ autoFocus = false }: { autoFocus?: boolean }) {
  const location = useLocation();
  const [text, setText] = useState('');
  const [files, setFiles] = useState<Attachment[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const send = useLolaStore((s) => s.send);
  const interrupt = useLolaStore((s) => s.interrupt);
  const streaming = useLolaStore((s) => !!s.streamingId);
  const lang = useLolaStore((s) => s.language);
  const voiceState = useLolaStore((s) => s.voiceState);
  const orb = useOrbState();
  const voice = useVoice(location.pathname);
  const input = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => { if (autoFocus) input.current?.focus(); }, [autoFocus]);

  const submit = () => {
    const v = text.trim();
    if (!v) return;
    voice.markTyped();
    stopSpeaking();
    setText('');
    const att = files;
    setFiles([]);
    void send(v, { channel: 'text', attachments: att.length ? att : undefined, route: location.pathname });
  };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
  };

  const onFiles = async (list: FileList | null) => {
    setFileError(null);
    if (!list) return;
    const next: Attachment[] = [];
    for (const f of Array.from(list).slice(0, 3)) {
      if (f.size > MAX_FILE_BYTES) { setFileError(lang === 'es' ? `${f.name} supera 15 MB.` : `${f.name} is over 15 MB.`); continue; }
      const isText = /^text\/|json|csv/.test(f.type) || /\.(csv|txt|md|json)$/i.test(f.name);
      // PDFs, Excel and photos (scans are read by text recognition when the server has it on).
      const isDocument = /pdf|spreadsheetml|image\/(png|jpeg)/.test(f.type) || /\.(pdf|xlsx|png|jpe?g)$/i.test(f.name);
      const a: Attachment = { name: f.name, size: f.size, mime: f.type || 'application/octet-stream' };
      if (isText && f.size <= MAX_INLINE_BYTES) a.text = await f.text();
      if (isDocument && f.size <= MAX_EXTRACT_BYTES) {
        // The server reads PDF / Excel into text and keeps nothing.
        try {
          const extracted = await getTransport().extractFile?.(f);
          if (extracted) { next.push(extracted); continue; }
        } catch (err) {
          setFileError(err instanceof Error ? err.message : `${f.name}`);
          continue;
        }
      }
      next.push(a);
    }
    setFiles((cur) => [...cur, ...next].slice(0, 3));
    if (fileInput.current) fileInput.current.value = '';
  };

  const listening = voice.listening;
  const speaking = voiceState === 'speaking';

  return (
    <div className="border-t border-lola-line/80 bg-lola-navy/95 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-2.5 sm:px-4">
      {/* Live transcript & status */}
      <div aria-live="polite" className="min-h-0">
        {(listening || voice.interim) && (
          <div data-testid="live-transcript" className="mb-2 flex items-center gap-2 rounded-lg border border-lola-gold/30 bg-lola-gold/5 px-3 py-2 text-[13px] text-lola-text">
            <LolaOrb state="listening" size={18} />
            <span className={voice.interim ? '' : 'italic text-lola-mute'}>{voice.interim || orbStatusLabel('listening', lang)}</span>
          </div>
        )}
        {speaking && (
          <div className="mb-2 flex items-center justify-between gap-2 rounded-lg border border-lola-line px-3 py-1.5 text-[12.5px] text-lola-mute">
            <span>{orbStatusLabel('speaking', lang)}…</span>
            <button type="button" onClick={stopSpeaking} className="rounded px-2 py-1 text-lola-amber hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber">{t('stop', lang)}</button>
          </div>
        )}
        {(voice.error || fileError) && (
          <p role="alert" className="mb-2 text-[12.5px] text-lola-danger">{voice.error || fileError}</p>
        )}
        <span className="sr-only" data-testid="orb-status">{orbStatusLabel(orb, lang)}</span>
      </div>

      {files.length > 0 && (
        <ul className="mb-2 flex flex-wrap gap-1.5" aria-label={lang === 'es' ? 'Adjuntos' : 'Attachments'}>
          {files.map((f) => (
            <li key={f.name} className="inline-flex items-center gap-1 rounded-md border border-lola-line bg-white/[0.03] py-1 pl-2 pr-1 text-[12px] text-lola-text flex-wrap">
              <FileText size={12} aria-hidden />{f.name}
              <button type="button" aria-label={`Remove ${f.name}`} onClick={() => setFiles((c) => c.filter((x) => x.name !== f.name))} className="rounded p-0.5 text-lola-mute hover:text-lola-text"><X size={12} /></button>
              {f.ocr && (
                <span data-testid="ocr-note" className="basis-full text-[11px] text-lola-amber">
                  {f.note ?? (lang === 'es'
                    ? 'Leído con reconocimiento de texto. Compare los números con el original.'
                    : 'Read with text recognition. Check the numbers against the original.')}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-end gap-1.5 rounded-2xl border border-lola-line bg-lola-deep px-1.5 py-1.5 focus-within:border-lola-gold/60">
        <input ref={fileInput} type="file" multiple className="sr-only" tabIndex={-1} aria-hidden onChange={(e) => onFiles(e.target.files)} />
        <button
          type="button"
          aria-label={t('attach', lang)}
          title={t('attach', lang)}
          onClick={() => fileInput.current?.click()}
          className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl text-lola-mute hover:bg-white/5 hover:text-lola-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber"
        >
          <Paperclip size={18} />
        </button>
        <label htmlFor="lola-input" className="sr-only">{t('askPlaceholder', lang)}</label>
        <textarea
          id="lola-input"
          ref={input}
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder={t('askPlaceholder', lang)}
          className="max-h-32 min-h-[40px] flex-1 resize-none bg-transparent px-1 py-2.5 text-[15px] text-lola-text placeholder:text-lola-mute/80 focus:outline-none"
        />
        <button
          type="button"
          onClick={voice.toggle}
          aria-pressed={listening}
          aria-label={listening ? t('micStop', lang) : t('micStart', lang)}
          title={listening ? t('micStop', lang) : t('micStart', lang)}
          data-testid="mic-button"
          className={`flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber ${listening ? 'bg-lola-gold text-lola-ink' : 'text-lola-amber hover:bg-white/5'}`}
        >
          {listening ? <MicOff size={18} /> : <Mic size={18} />}
        </button>
        {streaming && !text.trim() ? (
          <button
            type="button"
            onClick={interrupt}
            aria-label={t('stop', lang)}
            title={t('stop', lang)}
            className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl border border-lola-line text-lola-text hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber"
          >
            <Square size={14} fill="currentColor" />
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!text.trim()}
            aria-label={t('send', lang)}
            title={t('send', lang)}
            className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-lola-gold text-lola-ink hover:bg-lola-amber disabled:cursor-not-allowed disabled:opacity-35 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lola-amber"
          >
            <ArrowUp size={18} />
          </button>
        )}
      </div>
    </div>
  );
}
