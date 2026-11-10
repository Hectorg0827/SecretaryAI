import type React from 'react';
/**
 * Global Lola presence: a floating orb on every page (bottom-right, out of
 * the way) that expands into a conversation panel, or into the full command
 * center at /lola. Shortcut: Ctrl/⌘ + K. Esc closes.
 */
import { useCallback, useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useLolaStore } from '../store';
import { orbStatusLabel, t } from '../persona';
import { canUseLola } from '../permissions';
import { useAuth } from '../../hooks/useAuth';
import { LolaOrb } from './LolaOrb';
import { useOrbState } from './useOrbState';
import { LolaHeader } from './Header';
import { Composer, MessageList } from './Conversation';
import '../lola.css';

export function LolaDock() {
  const { role } = useAuth();
  const mode = useLolaStore((s) => s.mode);
  const open = useLolaStore((s) => s.open);
  const close = useLolaStore((s) => s.close);
  const lang = useLolaStore((s) => s.language);
  const step = useLolaStore((s) => s.workingStep);
  const orb = useOrbState();
  const location = useLocation();
  const navigate = useNavigate();
  const launcher = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const onCommandCenter = location.pathname === '/lola' || location.pathname === '/chat';
  const isOpen = mode === 'panel' && !onCommandCenter;

  // Return focus to the launcher whenever the panel closes (WCAG 2.4.3).
  const wasOpen = useRef(false);
  useEffect(() => {
    if (wasOpen.current && !isOpen) launcher.current?.focus();
    wasOpen.current = isOpen;
  }, [isOpen]);

  const doClose = useCallback(() => { close(); }, [close]);

  // Global shortcut.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (onCommandCenter) { document.getElementById('lola-input')?.focus(); return; }
        if (useLolaStore.getState().mode === 'panel') doClose(); else open('panel');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [doClose, open, onCommandCenter]);

  // Simple focus containment while the panel is open on small screens.
  const onPanelKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') { e.preventDefault(); doClose(); return; }
    if (e.key !== 'Tab' || window.innerWidth >= 640) return;
    const f = panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]),textarea,select,input:not([tabindex="-1"]),a[href]');
    if (!f || f.length === 0) return;
    const first = f[0];
    const last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  if (!canUseLola(role)) return null;

  return (
    <>
      {!isOpen && !onCommandCenter && (
        <button
          ref={launcher}
          type="button"
          data-testid="lola-launcher"
          onClick={() => open('panel')}
          aria-label={`${t('openLola', lang)} — ${orbStatusLabel(orb, lang, step)}`}
          aria-keyshortcuts="Control+K Meta+K"
          className="group fixed bottom-4 right-4 z-40 flex items-center gap-2 rounded-full bg-lola-navy/95 p-1.5 pr-1.5 shadow-lola-orb ring-1 ring-lola-gold/25 backdrop-blur transition-[padding] hover:pr-4 focus-visible:pr-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lola-amber sm:bottom-6 sm:right-6 print:hidden"
        >
          <LolaOrb state={orb} size={46} />
          <span className="hidden max-w-0 overflow-hidden whitespace-nowrap font-serif text-[15px] text-lola-text transition-[max-width] duration-300 group-hover:max-w-[160px] group-focus-visible:max-w-[160px] sm:inline">
            {orb === 'idle' ? (lang === 'es' ? 'Pregúntale a Lola' : 'Ask Lola') : orbStatusLabel(orb, lang, step)}
          </span>
        </button>
      )}

      {isOpen && (
        <div
          ref={panel}
          role="dialog"
          aria-labelledby="lola-title-panel"
          data-testid="lola-panel"
          onKeyDown={onPanelKey}
          className="lola-enter fixed inset-0 z-50 flex flex-col overflow-hidden bg-lola-navy text-lola-text sm:inset-auto sm:bottom-6 sm:right-6 sm:h-[min(760px,calc(100vh-48px))] sm:w-[440px] sm:rounded-2xl sm:shadow-lola sm:ring-1 sm:ring-lola-gold/15"
        >
          <LolaHeader variant="panel" onClose={doClose} onToggleSize={() => { close(); navigate('/lola'); }} />
          <MessageList compactEmpty />
          <Composer autoFocus />
        </div>
      )}
    </>
  );
}
