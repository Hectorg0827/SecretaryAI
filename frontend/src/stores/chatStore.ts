import { create } from 'zustand';
import { api } from '../lib/api';

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  streaming?: boolean;
}

export interface ConversationSummary {
  conversation_id: string;
  preview: string;
  created_at?: string | null;
}

interface ChatStore {
  messages: Message[];
  isStreaming: boolean;
  conversationId: string | null;
  conversations: ConversationSummary[];
  historyLoading: boolean;
  historyError: string | null;
  sendMessage: (text: string) => Promise<void>;
  clearConversation: () => void;
  loadConversations: () => Promise<void>;
  openConversation: (id: string) => Promise<boolean>;
}

let msgIdCounter = 0;
const nextId = () => String(++msgIdCounter);

export const useChatStore = create<ChatStore>((set, get) => ({
  messages: [],
  isStreaming: false,
  conversationId: null,
  conversations: [],
  historyLoading: false,
  historyError: null,

  loadConversations: async () => {
    set({ historyLoading: true, historyError: null });
    try {
      const data = await api.get<unknown>('/api/chat/conversations?limit=20');
      const value = data as { conversations?: unknown } | null;
      if (!value || !Array.isArray(value.conversations)) throw new Error('Invalid history response');
      const conversations = value.conversations.filter((item): item is ConversationSummary => (
        item !== null && typeof item === 'object'
        && typeof item.conversation_id === 'string'
        && typeof item.preview === 'string'
      ));
      set({ conversations });
    } catch {
      set({ historyError: 'Conversation history is unavailable. Try again.' });
    } finally {
      set({ historyLoading: false });
    }
  },

  openConversation: async (id: string) => {
    if (get().isStreaming || !id) return false;
    set({ historyLoading: true, historyError: null });
    try {
      const data = await api.get<unknown>(`/api/chat/history/${encodeURIComponent(id)}?limit=100`);
      const value = data as { conversation_id?: unknown; messages?: unknown } | null;
      if (!value || value.conversation_id !== id || !Array.isArray(value.messages)) {
        throw new Error('Invalid conversation response');
      }
      const messages: Message[] = value.messages
        .filter((item): item is { role: 'user' | 'assistant'; content: string; timestamp?: string } => {
          if (!item || typeof item !== 'object') return false;
          const record = item as Record<string, unknown>;
          return (record.role === 'user' || record.role === 'assistant')
            && typeof record.content === 'string';
        })
        .map((message) => ({
          id: nextId(),
          role: message.role,
          content: message.content,
          timestamp: message.timestamp && !Number.isNaN(Date.parse(message.timestamp))
            ? new Date(message.timestamp) : new Date(),
        }));
      set({ conversationId: id, messages });
      return true;
    } catch {
      set({ historyError: 'This conversation could not be opened.' });
      return false;
    } finally {
      set({ historyLoading: false });
    }
  },

  sendMessage: async (text: string) => {
    if (get().isStreaming) return;
    const trimmed = text.trim();
    if (!trimmed) return;
    const userMsg: Message = {
      id: nextId(), role: 'user', content: trimmed, timestamp: new Date(),
    };
    const assistantMsg: Message = {
      id: nextId(), role: 'assistant', content: '', timestamp: new Date(), streaming: true,
    };
    set((state) => ({
      messages: [...state.messages, userMsg, assistantMsg],
      isStreaming: true,
    }));

    let succeeded = false;
    try {
      for await (const event of api.streamChat(trimmed, get().conversationId ?? undefined)) {
        if (event.conversationId && !get().conversationId) {
          set({ conversationId: event.conversationId });
        }
        if (event.text) {
          set((state) => ({
            messages: state.messages.map((message) =>
              message.id === assistantMsg.id
                ? { ...message, content: message.content + event.text }
                : message),
          }));
        }
      }
      succeeded = true;
    } catch (error) {
      const reason = error instanceof Error ? error.message : '';
      const friendly = /Session expired/.test(reason)
        ? 'Your session has expired. Please sign in again.'
        : /Conversation not found/.test(reason)
          ? 'This conversation is no longer accessible. Start a new conversation.'
          : 'Lola could not complete or save this response. Please try again.';
      set((state) => ({
        messages: state.messages.map((message) =>
          message.id === assistantMsg.id
            ? { ...message, content: friendly }
            : message),
      }));
    } finally {
      set((state) => ({
        isStreaming: false,
        messages: state.messages.map((message) =>
          message.id === assistantMsg.id ? { ...message, streaming: false } : message),
      }));
    }
    if (succeeded) void get().loadConversations();
  },

  clearConversation: () => {
    if (get().isStreaming) return;
    set({ messages: [], conversationId: null, historyError: null });
  },
}));
