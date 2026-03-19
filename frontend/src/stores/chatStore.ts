import { create } from 'zustand';
import { api } from '../lib/api';

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  streaming?: boolean;
}

interface ChatStore {
  messages: Message[];
  isStreaming: boolean;
  conversationId: string | null;
  sendMessage: (text: string) => Promise<void>;
  clearConversation: () => void;
}

let msgIdCounter = 0;
const nextId = () => String(++msgIdCounter);

export const useChatStore = create<ChatStore>((set, get) => ({
  messages: [],
  isStreaming: false,
  conversationId: null,

  sendMessage: async (text: string) => {
    if (get().isStreaming) return;

    const userMsg: Message = {
      id: nextId(),
      role: 'user',
      content: text,
      timestamp: new Date(),
    };

    const assistantMsg: Message = {
      id: nextId(),
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      streaming: true,
    };

    set((s) => ({
      messages: [...s.messages, userMsg, assistantMsg],
      isStreaming: true,
    }));

    try {
      for await (const chunk of api.streamChat(text, get().conversationId ?? undefined)) {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === assistantMsg.id
              ? { ...m, content: m.content + chunk }
              : m,
          ),
        }));
      }
    } catch (error) {
      set((s) => ({
        messages: s.messages.map((m) =>
          m.id === assistantMsg.id
            ? { ...m, content: 'Sorry, something went wrong. Please try again.' }
            : m,
        ),
      }));
    } finally {
      set((s) => ({
        isStreaming: false,
        messages: s.messages.map((m) =>
          m.id === assistantMsg.id ? { ...m, streaming: false } : m,
        ),
      }));
    }
  },

  clearConversation: () => set({ messages: [], conversationId: null }),
}));
