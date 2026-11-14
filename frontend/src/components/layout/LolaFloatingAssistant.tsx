import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { X } from 'lucide-react';
import { useAuth } from '../../hooks/useAuth';
import { workspaceFromPath, type LolaContext } from '../../lib/lolaContext';
import { useLolaPanelStore } from '../../stores/lolaPanelStore';
import { LolaGlyph } from '../lola/LolaGlyph';
import { ChatInterface } from '../chat/ChatInterface';
import './lola-floating.css';

/** One floating entry point for operational pages; never a second chat system. */
export function LolaFloatingAssistant() {
  const { pathname } = useLocation();
  const { canAccessChat } = useAuth();
  const open = useLolaPanelStore((state) => state.open);
  const context = useLolaPanelStore((state) => state.context);
  const openPanel = useLolaPanelStore((state) => state.openPanel);
  const closePanel = useLolaPanelStore((state) => state.closePanel);
  const panelRef = useRef<HTMLElement>(null);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const priorPath = useRef(pathname);
  const currentWorkspace = workspaceFromPath(pathname);

  useEffect(() => {
    if (priorPath.current !== pathname) closePanel();
    priorPath.current = pathname;
  }, [pathname, closePanel]);

  useEffect(() => {
    if (!open) return;
    panelRef.current?.querySelector<HTMLTextAreaElement>('textarea')?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closePanel();
        launcherRef.current?.focus();
      }
    };
    document.addEventListener('keydown', dismiss);
    return () => document.removeEventListener('keydown', dismiss);
  }, [open, closePanel]);

  if (!canAccessChat || pathname === '/' || !currentWorkspace) return null;
  const pageContext: LolaContext = { workspace: currentWorkspace };
  const close = () => {
    closePanel();
    launcherRef.current?.focus();
  };

  return (
    <>
      <button type="button" ref={launcherRef} className="lola-float__launcher"
        aria-label={open ? 'Lola assistant is open' : 'Ask Lola about this page'}
        aria-expanded={open} aria-controls="lola-contextual-panel"
        onClick={() => open ? close() : openPanel(pageContext)}>
        <LolaGlyph size={27} /><span>Lola</span>
      </button>
      {open && (
        <aside id="lola-contextual-panel" ref={panelRef} role="dialog" aria-modal="false"
          aria-label="Lola contextual assistant" className="lola-float__panel">
          <ChatInterface compact onClose={close} context={context ?? pageContext} />
          <button type="button" className="lola-float__mobile-close" onClick={close}
            aria-label="Close Lola assistant"><X size={16} /> Close</button>
        </aside>
      )}
    </>
  );
}
