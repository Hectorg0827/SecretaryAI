import type React from 'react';
/**
 * Executive command center — Lola's full-screen home. Role-aware briefing on
 * the left, the live conversation on the right (stacked on mobile).
 */
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertOctagon, BarChart3, Briefcase, CheckCircle2, ChevronRight, ClipboardCheck, CloudOff, Lock,
  RefreshCw, Sparkles, Sunrise, Truck, Wallet, ListChecks,
} from 'lucide-react';
import { getTransport, useLolaStore } from '../store';
import { useAuth } from '../../hooks/useAuth';
import { canUseLola } from '../permissions';
import { greeting, t } from '../persona';
import type { Briefing, BriefingItem, BriefingSection, BriefingSectionId } from '../contract';
import { LolaHeader } from './Header';
import { Composer, MessageList } from './Conversation';
import { LolaOrb } from './LolaOrb';
import { useOrbState } from './useOrbState';
import '../lola.css';

const ICON: Record<BriefingSectionId, React.ElementType> = {
  morning_briefing: Sunrise,
  critical_alerts: AlertOctagon,
  performance: BarChart3,
  department_priorities: Briefcase,
  logistics_exceptions: Truck,
  financial_risks: Wallet,
  pending_approvals: ClipboardCheck,
  next_actions: Sparkles,
  recent_tasks: ListChecks,
};

const SEV_DOT: Record<NonNullable<BriefingItem['severity']>, { cls: string; sr: string }> = {
  critical: { cls: 'bg-lola-danger', sr: 'Critical' },
  warning: { cls: 'bg-amber-400', sr: 'Warning' },
  info: { cls: 'bg-[#5E8BE0]', sr: 'Info' },
  good: { cls: 'bg-lola-ok', sr: 'Good' },
};

const ES_TITLES: Record<BriefingSectionId, string> = {
  morning_briefing: 'Resumen de la mañana',
  critical_alerts: 'Alertas críticas',
  performance: 'Desempeño del negocio',
  department_priorities: 'Prioridades por área',
  logistics_exceptions: 'Excepciones de logística',
  financial_risks: 'Riesgos financieros',
  pending_approvals: 'Aprobaciones pendientes',
  next_actions: 'Siguientes pasos sugeridos',
  recent_tasks: 'Completado recientemente',
};

function Section({ s, onAsk, lang }: { s: BriefingSection; onAsk: (q: string) => void; lang: 'en' | 'es' }) {
  const Icon = ICON[s.id];
  const blocked = s.availability === 'unavailable' || s.availability === 'forbidden';
  const wide = s.id === 'critical_alerts' || s.id === 'morning_briefing';
  return (
    <section
      aria-labelledby={`sec-${s.id}`}
      data-section={s.id}
      data-availability={s.availability}
      className={`rounded-2xl border bg-lola-panel/70 p-4 ${blocked ? 'border-dashed border-lola-line' : 'border-lola-line'} ${wide ? 'md:col-span-2' : ''}`}
    >
      <h2 id={`sec-${s.id}`} className="mb-2.5 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.14em] text-lola-gold">
        <Icon size={14} aria-hidden />{lang === 'es' ? ES_TITLES[s.id] : s.title}
      </h2>
      {blocked ? (
        <p className="flex items-start gap-2 text-[13px] text-lola-mute">
          {s.availability === 'forbidden' ? <Lock size={14} className="mt-0.5" aria-hidden /> : <CloudOff size={14} className="mt-0.5" aria-hidden />}
          <span><strong className="font-semibold text-lola-text/90">{s.availability === 'forbidden' ? (lang === 'es' ? 'Restringido.' : 'Restricted.') : (lang === 'es' ? 'No disponible.' : 'Unavailable.')}</strong> {s.availability_note}</span>
        </p>
      ) : (
        <>
          {s.availability !== 'ok' && (
            <p className="mb-2 text-[12px] text-amber-300">{s.availability === 'stale' ? (lang === 'es' ? 'Puede estar desactualizado. ' : 'May be out of date. ') : ''}{s.availability_note}</p>
          )}
          {s.items.length === 0 ? (
            <p className="flex items-center gap-1.5 text-[13px] text-lola-mute"><CheckCircle2 size={14} className="text-lola-ok" aria-hidden />{lang === 'es' ? 'Nada pendiente aquí.' : 'Nothing needs you here.'}</p>
          ) : (
            <ul className="space-y-1">
              {s.items.map((it) => {
                const sev = it.severity ? SEV_DOT[it.severity] : null;
                const body = (
                  <>
                    {sev && <span className={`mt-1.5 h-2 w-2 flex-shrink-0 rounded-full ${sev.cls}`} aria-hidden />}
                    <span className="min-w-0 flex-1">
                      {sev && <span className="sr-only">{sev.sr}: </span>}
                      <span className="block text-[13.5px] text-lola-text">{it.title}</span>
                      {(it.detail || it.meta) && <span className="block text-[12px] text-lola-mute">{[it.detail, it.meta].filter(Boolean).join(' · ')}</span>}
                    </span>
                    {(it.ask || it.href) && <ChevronRight size={15} className="mt-1 flex-shrink-0 text-lola-mute group-hover:text-lola-amber" aria-hidden />}
                  </>
                );
                const cls = 'group flex w-full items-start gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber';
                return (
                  <li key={it.id}>
                    {it.ask ? (
                      <button type="button" className={cls} onClick={() => onAsk(it.ask!)} aria-label={`${it.title}. ${lang === 'es' ? 'Preguntar a Lola' : 'Ask Lola'}`}>{body}</button>
                    ) : it.href ? (
                      <Link to={it.href} className={cls}>{body}</Link>
                    ) : (
                      <div className="flex items-start gap-2.5 px-2 py-1.5">{body}</div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

export function CommandCenter() {
  const { role } = useAuth();
  const lang = useLolaStore((s) => s.language);
  const send = useLolaStore((s) => s.send);
  const transportKind = useLolaStore((s) => s.transportKind);
  const orb = useOrbState();
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');

  const load = useCallback(() => {
    const ctrl = new AbortController();
    setState('loading');
    getTransport().briefing(role, ctrl.signal)
      .then((b) => { setBriefing(b); setState('ready'); })
      .catch(() => { if (!ctrl.signal.aborted) setState('error'); });
    return () => ctrl.abort();
  }, [role]);

  useEffect(() => load(), [load]);

  if (!canUseLola(role)) {
    return (
      <div className="flex h-full items-center justify-center bg-lola-ink p-6 text-center text-lola-mute">
        <p>{lang === 'es' ? 'Lola no está disponible para tu rol.' : 'Lola isn’t available for your role.'}</p>
      </div>
    );
  }

  const ask = (q: string) => {
    void send(q);
    if (window.innerWidth < 1024) document.getElementById('lola-conversation')?.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <div className="h-full overflow-y-auto bg-lola-ink text-lola-text lg:overflow-hidden lola-scroll" data-testid="command-center">
      <div className="mx-auto flex min-h-full max-w-[1500px] flex-col lg:h-full lg:flex-row">
        {/* Briefing */}
        <div className="flex-1 px-4 pb-6 pt-5 sm:px-6 lg:overflow-y-auto lola-scroll">
          <div className="relative mb-5 overflow-hidden rounded-3xl border border-lola-line bg-[radial-gradient(120%_140%_at_0%_0%,#1B2A4A_0%,#0B1324_55%,#070B14_100%)] p-5 sm:p-7">
            <div className="pointer-events-none absolute -right-10 -top-16 h-56 w-56 rounded-full bg-lola-gold/10 blur-3xl" aria-hidden />
            <div className="flex items-center gap-4 sm:gap-5">
              <LolaOrb state={orb} size={72} className="flex-shrink-0" />
              <div className="min-w-0">
                <p className="text-[12px] font-semibold uppercase tracking-[0.16em] text-lola-gold">
                  {lang === 'es' ? 'Centro de mando' : 'Command center'}
                  {(briefing?.demo || transportKind === 'mock') && <span data-testid="briefing-demo-badge" className="ml-2 rounded-full border border-lola-gold/50 px-1.5 py-px text-[10px] text-lola-amber">{t('demo', lang)}</span>}
                </p>
                <h1 className="mt-1 font-serif text-[26px] leading-tight text-lola-text sm:text-[32px]">{greeting(lang)}</h1>
                <p className="mt-1 text-[14.5px] text-lola-text/80" aria-live="polite">
                  {state === 'loading' ? (lang === 'es' ? 'Preparando tu resumen…' : 'Preparing your briefing…') : state === 'error' ? (lang === 'es' ? 'No pude preparar el resumen.' : 'I couldn’t prepare your briefing.') : briefing?.headline}
                </p>
              </div>
              <button type="button" onClick={load} aria-label={lang === 'es' ? 'Actualizar resumen' : 'Refresh briefing'} className="ml-auto hidden h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg text-lola-mute hover:bg-white/5 hover:text-lola-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber sm:flex">
                <RefreshCw size={16} className={state === 'loading' ? 'animate-spin motion-reduce:animate-none' : ''} />
              </button>
            </div>
            <div className="mt-5 flex flex-wrap gap-2">
              <button type="button" onClick={() => ask(lang === 'es' ? '¿Qué está pasando con mi negocio?' : 'What’s happening with my business?')} className="min-h-[40px] rounded-full bg-lola-gold px-4 text-[13.5px] font-semibold text-lola-ink hover:bg-lola-amber focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lola-amber">
                {lang === 'es' ? 'Dame el resumen' : 'Brief me'}
              </button>
              <Link to="/" className="inline-flex min-h-[40px] items-center rounded-full border border-lola-line px-4 text-[13.5px] text-lola-text/90 hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber">
                {lang === 'es' ? 'Ver tablero detallado' : 'Open detailed dashboard'}
              </Link>
            </div>
          </div>

          {state === 'error' && (
            <div role="alert" className="mb-4 rounded-2xl border border-lola-danger/40 bg-lola-danger/5 p-4 text-[13.5px]">
              {lang === 'es' ? 'No pude conectar con Lion BI. Ninguna sección se muestra como “sin pendientes” hasta confirmarlo.' : 'I couldn’t reach Lion BI. No section is shown as “all clear” until I can confirm it.'}
              <button type="button" onClick={load} className="ml-2 underline">{t('retry', lang)}</button>
            </div>
          )}

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2" aria-busy={state === 'loading'}>
            {state === 'loading' && Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-36 animate-pulse rounded-2xl border border-lola-line bg-lola-panel/50 motion-reduce:animate-none" />
            ))}
            {state === 'ready' && briefing?.sections.map((s) => <Section key={s.id} s={s} onAsk={ask} lang={lang} />)}
          </div>
        </div>

        {/* Conversation */}
        <aside
          id="lola-conversation"
          aria-labelledby="lola-title-full"
          className="flex h-[78vh] min-h-[480px] flex-col border-t border-lola-line bg-lola-navy lg:h-full lg:w-[460px] lg:flex-shrink-0 lg:border-l lg:border-t-0 xl:w-[520px]"
        >
          <LolaHeader variant="full" />
          <MessageList compactEmpty />
          <Composer />
        </aside>
      </div>
    </div>
  );
}
