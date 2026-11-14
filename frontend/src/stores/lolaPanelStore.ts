import { create } from 'zustand';
import type { LolaContext } from '../lib/lolaContext';
import { contextKey } from '../lib/lolaContext';
import { useChatStore } from './chatStore';

interface LolaPanelState {
  open: boolean;
  context: LolaContext | null;
  openPanel: (context: LolaContext) => void;
  closePanel: () => void;
}

export const useLolaPanelStore = create<LolaPanelState>((set) => ({
  open: false,
  context: null,
  openPanel: (context) => {
    const chat = useChatStore.getState();
    if (chat.isStreaming && contextKey(chat.context) !== contextKey(context)) return;
    chat.beginContext(context);
    set({ open: true, context });
  },
  closePanel: () => set({ open: false }),
}));
