import { useEffect, useRef, useState } from 'react';
import { History, Maximize2, Minimize2, Plus, Settings2, X } from 'lucide-react';
import { useLolaStore } from '../store';
import { useLolaPrefs } from '../settings';
import { orbStatusLabel, t } from '../persona';
import { api } from '../../lib/api';
import { LolaOrb } from './LolaOrb';
import { useOrbState } from './useOrbState';
import type { LolaLanguage } from '../contract';

const iconBtn = 'flex h-9 w-9 items-center justify-center rounded-lg text-lola-mute hover:bg-white/5 hover:text-lola-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber';

export function LanguageToggle() {
  const lang = useLolaStore((s) => s.language);
  const setLanguage = useLolaStore((s) => s.setLanguage);
  const update = useLolaPrefs((s) => s.update);
  const pick = (l: LolaLanguage) => { setLanguage(l); update({ language: l }); };
  return (
    <div role="group" aria-label="Language / Idioma" className="flex rounded-lg border border-lola-line p-0.5 text-[11px] font-semibold">
      {(['en', 'es'] as const).map((l) => (
        <button key={l} type="button" aria-pressed={lang === l} onClick={() => pick(l)} className={`min-h-[28px] rounded-md px-2 uppercase focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber ${lang === l ? 'bg-lola-gold text-lola-ink' : 'text-lola-mute hover:text-lola-text'}`}>
          {l}
        </button>
      ))}
    </div>
  );
}

export function SettingsPanel({ onClose }: { onClose: () => void }) {
  const prefs = useLolaPrefs();
  const lang = useLolaStore((s) => s.language);
  const transport = useLolaStore((s) => s.transportKind);
  const es = lang === 'es';
  const row = 'flex items-center justify-between gap-3 py-2';
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.querySelector<HTMLElement>('select,input')?.focus(); }, []);
  return (
    <div ref={ref} role="dialog" aria-label={t('settings', lang)} className="absolute right-3 top-14 z-20 w-[min(340px,calc(100vw-24px))] rounded-xl border border-lola-line bg-lola-navy p-4 text-[13px] text-lola-text shadow-lola lola-enter"
      onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } }}>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="font-semibold">{t('settings', lang)}</h3>
        <button type="button" onClick={onClose} aria-label={es ? 'Cerrar ajustes' : 'Close settings'} className={iconBtn}><X size={15} /></button>
      </div>
      <label className={row}>
        <span>{es ? 'Idioma' : 'Language'}</span>
        <select value={prefs.language} onChange={(e) => prefs.update({ language: e.target.value as typeof prefs.language })} className="rounded-md border border-lola-line bg-lola-deep px-2 py-1">
          <option value="auto">{es ? 'Automático' : 'Automatic'}</option>
          <option value="en">English</option>
          <option value="es">Español</option>
        </select>
      </label>
      <label className={row}>
        <span>{es ? 'Leer respuestas en voz alta' : 'Read replies aloud'}<span className="block text-[11px] text-lola-mute">{es ? 'Solo cuando preguntas por voz' : 'Only after a voice question'}</span></span>
        <input type="checkbox" checked={prefs.speakReplies} onChange={(e) => prefs.update({ speakReplies: e.target.checked })} className="h-4 w-4 accent-[#D6A84E]" />
      </label>
      <label className={row}>
        <span>{es ? 'Conversación continua' : 'Hands-free follow-ups'}<span className="block text-[11px] text-lola-mute">{es ? 'Reabre el micrófono al terminar de hablar' : 'Re-opens the mic after Lola speaks'}</span></span>
        <input type="checkbox" checked={prefs.continuousConversation} onChange={(e) => prefs.update({ continuousConversation: e.target.checked })} className="h-4 w-4 accent-[#D6A84E]" />
      </label>
      <label className={row}>
        <span>{es ? 'Transcripciones' : 'Transcripts'}</span>
        <select value={prefs.transcriptRetention} onChange={(e) => prefs.update({ transcriptRetention: e.target.value as typeof prefs.transcriptRetention })} className="rounded-md border border-lola-line bg-lola-deep px-2 py-1">
          <option value="none">{es ? 'No guardar' : 'Don’t keep'}</option>
          <option value="session">{es ? 'Solo esta sesión' : 'This session only'}</option>
          <option value="saved">{es ? 'Guardar en mi cuenta' : 'Save to my account'}</option>
        </select>
      </label>
      <label className={row}>
        <span>{es ? 'Grabaciones de voz' : 'Voice recordings'}</span>
        <select value={prefs.audioRetention} onChange={(e) => prefs.update({ audioRetention: e.target.value as typeof prefs.audioRetention })} className="rounded-md border border-lola-line bg-lola-deep px-2 py-1">
          <option value="none">{es ? 'Nunca guardar' : 'Never keep'}</option>
          <option value="30d">{es ? '30 días (calidad)' : '30 days (quality review)'}</option>
        </select>
      </label>
      <p className="mt-2 text-[11px] leading-snug text-lola-mute">
        {es
          ? 'El micrófono solo se activa cuando lo tocas. Tu empresa puede aplicar reglas de retención más estrictas.'
          : 'The mic only turns on when you tap it. Your company’s retention policy can be stricter than these choices.'}
        {transport === 'mock' && <><br /><strong className="text-lola-amber">{t('demo', lang)}</strong> — {es ? 'respuestas de ejemplo, no datos reales.' : 'scripted answers, not your real data.'}</>}
      </p>
    </div>
  );
}

export function HistoryPanel({ onClose }: { onClose: () => void }) {
  const lang = useLolaStore((s) => s.language);
  const load = useLolaStore((s) => s.loadConversation);
  const transport = useLolaStore((s) => s.transportKind);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [items, setItems] = useState<{ conversation_id: string; preview: string; created_at: string }[]>([]);
  const es = lang === 'es';
  useEffect(() => {
    if (transport === 'mock') { setState('ready'); return; }
    api.get<{ conversations: typeof items }>('/api/chat/conversations')
      .then((r) => { setItems(r.conversations ?? []); setState('ready'); })
      .catch(() => setState('error'));
  }, [transport]);
  const open = async (id: string) => {
    try {
      const r = await api.get<{ messages: { role: string; content: string; created_at: string }[] }>(`/api/chat/history/${encodeURIComponent(id)}`);
      load(id, r.messages ?? []);
      onClose();
    } catch { setState('error'); }
  };
  return (
    <div role="dialog" aria-label={t('history', lang)} className="absolute right-3 top-14 z-20 w-[min(340px,calc(100vw-24px))] rounded-xl border border-lola-line bg-lola-navy p-3 text-[13px] text-lola-text shadow-lola lola-enter"
      onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } }}>
      <div className="mb-1 flex items-center justify-between px-1">
        <h3 className="font-semibold">{t('history', lang)}</h3>
        <button type="button" onClick={onClose} aria-label={es ? 'Cerrar' : 'Close'} className={iconBtn}><X size={15} /></button>
      </div>
      {state === 'loading' && <p className="px-1 py-2 text-lola-mute">{es ? 'Cargando…' : 'Loading…'}</p>}
      {state === 'error' && <p role="alert" className="px-1 py-2 text-lola-danger">{es ? 'No pude cargar el historial.' : 'Couldn’t load your history.'}</p>}
      {state === 'ready' && items.length === 0 && <p className="px-1 py-2 text-lola-mute">{transport === 'mock' ? (es ? 'El modo demo no guarda conversaciones.' : 'Demo mode doesn’t save conversations.') : (es ? 'Aún no hay conversaciones guardadas.' : 'No saved conversations yet.')}</p>}
      <ul className="max-h-72 overflow-y-auto lola-scroll">
        {items.map((c) => (
          <li key={c.conversation_id}>
            <button type="button" onClick={() => open(c.conversation_id)} className="w-full rounded-lg px-2 py-2 text-left hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber">
              <span className="block truncate">{c.preview}</span>
              <span className="text-[11px] text-lola-mute">{new Date(c.created_at).toLocaleString()}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

interface HeaderProps {
  variant: 'panel' | 'full';
  onClose?: () => void;
  onToggleSize?: () => void;
}

export function LolaHeader({ variant, onClose, onToggleSize }: HeaderProps) {
  const lang = useLolaStore((s) => s.language);
  const step = useLolaStore((s) => s.workingStep);
  const newConversation = useLolaStore((s) => s.newConversation);
  const transport = useLolaStore((s) => s.transportKind);
  const orb = useOrbState();
  const [pop, setPop] = useState<'none' | 'settings' | 'history'>('none');
  return (
    <header className="relative flex items-center gap-2.5 border-b border-lola-line/80 px-3 py-2.5 sm:px-4">
      <LolaOrb state={orb} size={variant === 'panel' ? 30 : 34} className="flex-shrink-0" />
      <div className="min-w-0 flex-1">
        <h2 id={`lola-title-${variant}`} className="flex items-center gap-2 font-serif text-[17px] leading-tight text-lola-text">
          Lola
          {transport === 'mock' && <span data-testid="demo-badge" className="whitespace-nowrap rounded-full border border-lola-gold/50 px-1.5 py-px font-sans text-[10px] font-semibold uppercase tracking-wider text-lola-amber">{t('demo', lang)}</span>}
        </h2>
        <p className="truncate text-[11.5px] text-lola-mute" aria-live="polite" data-testid="lola-status">{orbStatusLabel(orb, lang, step)}</p>
      </div>
      <LanguageToggle />
      <button type="button" className={iconBtn} aria-label={t('newConversation', lang)} title={t('newConversation', lang)} onClick={newConversation}><Plus size={17} /></button>
      {variant === 'full' && <button type="button" className={iconBtn} aria-label={t('history', lang)} title={t('history', lang)} aria-expanded={pop === 'history'} onClick={() => setPop(pop === 'history' ? 'none' : 'history')}><History size={16} /></button>}
      <button type="button" className={iconBtn} aria-label={t('settings', lang)} title={t('settings', lang)} aria-expanded={pop === 'settings'} onClick={() => setPop(pop === 'settings' ? 'none' : 'settings')}><Settings2 size={16} /></button>
      {onToggleSize && (
        <button type="button" className={`${iconBtn} hidden sm:flex`} aria-label={variant === 'panel' ? t('expand', lang) : (lang === 'es' ? 'Volver al panel' : 'Back to panel')} title={variant === 'panel' ? t('expand', lang) : undefined} onClick={onToggleSize}>
          {variant === 'panel' ? <Maximize2 size={16} /> : <Minimize2 size={16} />}
        </button>
      )}
      {onClose && <button type="button" className={iconBtn} aria-label={t('closeLola', lang)} title={t('closeLola', lang)} onClick={onClose}><X size={18} /></button>}
      {pop === 'settings' && <SettingsPanel onClose={() => setPop('none')} />}
      {pop === 'history' && <HistoryPanel onClose={() => setPop('none')} />}
    </header>
  );
}
