import { create } from 'zustand';
import { api } from '../lib/api';
import { cleanContext, contextKey, readContext, type LolaContext } from '../lib/lolaContext';

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
  context: LolaContext | null;
  conversations: ConversationSummary[];
  historyLoading: boolean;
  historyError: string | null;
  deletingId: string | null;
  sendMessage: (text: string) => Promise<void>;
  clearConversation: () => void;
  beginContext: (context: LolaContext | null) => void;
  loadConversations: () => Promise<void>;
  openConversation: (id: string) => Promise<boolean>;
  deleteConversation: (id: string) => Promise<boolean>;
}

let msgIdCounter = 0;
const nextId = () => String(++msgIdCounter);

/** Single source of truth for every Lola entry point: Home, contextual panel, history. */
export const useChatStore = create<ChatStore>((set, get) => ({
  messages: [],
  isStreaming: false,
  conversationId: null,
  context: null,
  conversations: [],
  historyLoading: false,
  historyError: null,
  deletingId: null,

  beginContext: (context) => {
    if (get().isStreaming) return;
    const clean = context ? cleanContext(context) : null;
    if (contextKey(get().context) === contextKey(clean)) return;
    // Switching workspaces starts a new conversation, while prior turns remain
    // saved on the server. Never relabel another page's conversation.
    set({ context: clean, messages: [], conversationId: null, historyError: null });
  },

  loadConversations: async () => {
    set({ historyLoading: true, historyError: null });
    try {
      const data = await api.get<unknown>('/api/chat/conversations?limit=100');
      const value = data as { conversations?: unknown } | null;
      if (!value || !Array.isArray(value.conversations)) throw new Error('Invalid history response');
      const conversations = value.conversations.filter((item): item is ConversationSummary => (
        item !== null && typeof item === 'object'
        && typeof item.conversation_id === 'string'
        && typeof item.preview === 'string'
      ));
      set({ conversations });
    } catch {
      set({ historyError: 'Conversation history is unavailable. Please try again.' });
    } finally {
      set({ historyLoading: false });
    }
  },

  openConversation: async (id) => {
    if (get().isStreaming || get().deletingId || !id) return false;
    set({ historyLoading: true, historyError: null });
    try {
      const data = await api.get<unknown>('/api/chat/history/' + encodeURIComponent(id) + '?limit=200');
      const value = data as { conversation_id?: unknown; messages?: unknown } | null;
      if (!value || value.conversation_id !== id || !Array.isArray(value.messages)) {
        throw new Error('Invalid conversation response');
      }
      const saved = value.messages.filter((item): item is { role: 'user' | 'assistant'; content: string;
        timestamp?: string; context?: unknown } => {
        if (!item || typeof item !== 'object') return false;
        const record = item as Record<string, unknown>;
        return (record.role === 'user' || record.role === 'assistant')
          && typeof record.content === 'string';
      });
      const messages: Message[] = saved.map((message) => ({
        id: nextId(),
        role: message.role,
        content: message.content,
        timestamp: message.timestamp && !Number.isNaN(Date.parse(message.timestamp))
          ? new Date(message.timestamp) : new Date(),
      }));
      const lastContext = [...saved].reverse().find((m) => m.role === 'user' && m.context);
      set({ conversationId: id, messages, context: readContext(lastContext?.context) });
      return true;
    } catch {
      set({ historyError: 'This conversation could not be opened.' });
      return false;
    } finally {
      set({ historyLoading: false });
    }
  },

  deleteConversation: async (id) => {
    if (!id || get().isStreaming || get().deletingId) return false;
    set({ deletingId: id, historyError: null });
    try {
      const result = await api.delete<{ deleted: boolean; conversation_id: string }>(
        '/api/chat/conversations/' + encodeURIComponent(id),
      );
      if (!result.deleted || result.conversation_id !== id) throw new Error('Unexpected deletion response');
      set((state) => ({
        conversations: state.conversations.filter((item) => item.conversation_id !== id),
        ...(state.conversationId === id ? { conversationId: null, messages: [] } : {}),
      }));
      return true;
    } catch {
      set({ historyError: 'Conversation could not be deleted. Please try again.' });
      return false;
    } finally {
      set({ deletingId: null });
    }
  },

  sendMessage: async (text) => {
    if (get().isStreaming || get().deletingId) return;
    const trimmed = text.trim();
    if (!trimmed) return;
    const conversationId = get().conversationId ?? undefined;
    const context = get().context ?? undefined;
    const userMsg: Message = { id: nextId(), role: 'user', content: trimmed, timestamp: new Date() };
    const assistantMsg: Message = {
      id: nextId(), role: 'assistant', content: '', timestamp: new Date(), streaming: true,
    };
    set((state) => ({ messages: [...state.messages, userMsg, assistantMsg], isStreaming: true }));
    let succeeded = false;
    try {
      for await (const event of api.streamChat(trimmed, conversationId, context)) {
        if (event.conversationId && !get().conversationId) set({ conversationId: event.conversationId });
        if (event.text) {
          set((state) => ({
            messages: state.messages.map((message) =>
              message.id === assistantMsg.id ? { ...message, content: message.content + event.text } : message),
          }));
        }
      }
      succeeded = true;
    } catch (error) {
      const reason = error instanceof Error ? error.message : '';
      const friendly = /Session expired/.test(reason)
        ? 'Your session expired. Please sign in again.'
        : /Conversation not found/.test(reason)
          ? 'This conversation is no longer accessible. Start a new conversation.'
          : 'Lola could not complete or save this response. Please try again.';
      set((state) => ({
        messages: state.messages.map((message) =>
          message.id === assistantMsg.id ? { ...message, content: friendly } : message),
      }));
    } finally {
      set((state) => ({
        isStreaming: false,
        messages: state.messages.map((message) =>
          message.id === assistantMsg.id ? { ...message, streaming: false } : message),
      }));
    }
    // Persisted server-side after each completed turn. Refresh history for discovery.
    if (succeeded) void get().loadConversations();
  },

  clearConversation: () => {
    if (get().isStreaming) return;
    set({ messages: [], conversationId: null, historyError: null });
  },
}));
