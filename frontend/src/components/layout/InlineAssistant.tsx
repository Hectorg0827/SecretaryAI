import { useLocation } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { workspaceFromPath, type LolaContext } from '../../lib/lolaContext';
import { useLolaPanelStore } from '../../stores/lolaPanelStore';
import { LolaGlyph } from '../lola/LolaGlyph';

interface AssistantContext {
  type: 'account' | 'inventory' | 'inbox' | 'work' | 'general';
  label?: string;
  /** Deliberately not sent to the AI; backend must fetch authorized records itself. */
  data?: Record<string, unknown>;
}
interface Props {
  context?: AssistantContext;
  initialSuggestion?: string;
  onClose?: () => void;
  className?: string;
}

/** Context trigger, not a competing mini-chat implementation. */
export function InlineAssistant({ context, onClose, className = '' }: Props) {
  const { pathname } = useLocation();
  const { canAccessChat } = useAuth();
  const openPanel = useLolaPanelStore((state) => state.openPanel);
  const workspace = workspaceFromPath(pathname);
  if (!canAccessChat || !workspace) return null;
  const target: LolaContext = {
    workspace,
    ...(workspace === 'accounts' && context?.type === 'account' ? { entity_type: 'account' as const } : {}),
    ...(workspace === 'inbox' ? { entity_type: 'item' as const } : {}),
    ...(context?.label ? { entity_label: context.label } : {}),
  };
  const label = context?.label ?? workspace;

  return (
    <div className={`px-3 py-3 ${className}`}>
      <button type="button"
        className="flex w-full items-center justify-between gap-3 rounded-xl border border-[#53607b] bg-[#202c45] px-3 py-3 text-left text-[#f1daab] transition hover:border-[#e2c18d] hover:bg-[#293852] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#ebca91]"
        onClick={() => { openPanel(target); onClose?.(); }}
        aria-label={`Ask Lola about ${label}`}>
        <span className="flex min-w-0 items-center gap-3">
          <LolaGlyph size={27} />
          <span className="min-w-0">
            <strong className="block text-sm">Ask Lola</strong>
            <small className="block truncate text-xs text-[#b6c5dd]">Context: {label}</small>
          </span>
        </span>
        <span aria-hidden="true" className="text-[#e8c990]">›</span>
      </button>
    </div>
  );
}
