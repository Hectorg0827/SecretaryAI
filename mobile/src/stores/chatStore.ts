import { create } from 'zustand';
import { ChatMessage, Conversation, streamChat, getConversations, getChatHistory } from '../api/chat';

interface LocalMessage extends ChatMessage {
  id: string;
  isStreaming?: boolean;
}

interface ChatState {
  conversations: Conversation[];
  currentConversationId: string | null;
  messages: LocalMessage[];
  isStreaming: boolean;
  error: string | null;

  // Actions
  loadConversations: () => Promise<void>;
  loadHistory: (conversationId: string) => Promise<void>;
  sendMessage: (text: string) => Promise<void>;
  newConversation: () => void;
  clearError: () => void;
}

let messageIdCounter = 0;
function nextId() {
  return `msg_${++messageIdCounter}_${Date.now()}`;
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  currentConversationId: null,
  messages: [],
  isStreaming: false,
  error: null,

  loadConversations: async () => {
    try {
      const data = await getConversations();
      set({ conversations: data.conversations });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to load conversations' });
    }
  },

  loadHistory: async (conversationId: string) => {
    try {
      const data = await getChatHistory(conversationId);
      const messages: LocalMessage[] = data.messages.map((m) => ({
        ...m,
        id: nextId(),
      }));
      set({ messages, currentConversationId: conversationId });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to load history' });
    }
  },

  sendMessage: async (text: string) => {
    const userMessage: LocalMessage = {
      id: nextId(),
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    };

    const assistantMessageId = nextId();
    const assistantMessage: LocalMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      isStreaming: true,
      created_at: new Date().toISOString(),
    };

    set((state) => ({
      messages: [...state.messages, userMessage, assistantMessage],
      isStreaming: true,
      error: null,
    }));

    try {
      let fullText = '';
      let conversationId = get().currentConversationId ?? undefined;

      for await (const chunk of streamChat(text, conversationId)) {
        if (chunk.conversation_id && !conversationId) {
          conversationId = chunk.conversation_id;
          set({ currentConversationId: conversationId });
        }
        if (chunk.text) {
          fullText += chunk.text;
          set((state) => ({
            messages: state.messages.map((m) =>
              m.id === assistantMessageId
                ? { ...m, content: fullText }
                : m,
            ),
          }));
        }
        if (chunk.done) break;
      }

      set((state) => ({
        messages: state.messages.map((m) =>
          m.id === assistantMessageId ? { ...m, isStreaming: false } : m,
        ),
        isStreaming: false,
      }));
    } catch (err) {
      set((state) => ({
        messages: state.messages.map((m) =>
          m.id === assistantMessageId
            ? {
                ...m,
                content: 'Sorry, something went wrong. Please try again.',
                isStreaming: false,
              }
            : m,
        ),
        isStreaming: false,
        error: err instanceof Error ? err.message : 'Chat error',
      }));
    }
  },

  newConversation: () => {
    set({ messages: [], currentConversationId: null, error: null });
  },

  clearError: () => set({ error: null }),
}));
