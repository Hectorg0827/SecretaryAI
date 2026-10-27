import { create } from 'zustand';
import { api } from '../lib/api';

export type ItemType = 'email' | 'alert' | 'approval' | 'health_event';
export type Priority = 'high' | 'medium' | 'low';

export interface InboxItem {
  id: string;
  type: ItemType;
  priority: Priority;
  title: string;
  subtitle: string;
  timestamp: string;
  is_read: boolean;
  payload: Record<string, unknown>;
}

function validInboxItem(value: unknown): value is InboxItem {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return typeof item.id === 'string' && item.id.length > 0
    && ['email', 'alert', 'approval', 'health_event'].includes(String(item.type))
    && ['high', 'medium', 'low'].includes(String(item.priority))
    && typeof item.title === 'string' && typeof item.subtitle === 'string'
    && typeof item.timestamp === 'string' && Number.isFinite(Date.parse(item.timestamp))
    && typeof item.is_read === 'boolean'
    && !!item.payload && typeof item.payload === 'object' && !Array.isArray(item.payload);
}

let latestFetchRequest = 0;

interface InboxState {
  items: InboxItem[];
  unread_count: number | null;
  loading: boolean;
  error: string | null;
  mutationError: string | null;
  selectedId: string | null;
  filter: 'all' | ItemType;

  fetch: () => Promise<void>;
  setFilter: (f: 'all' | ItemType) => void;
  selectItem: (id: string | null) => void;
  markRead: (id: string) => Promise<void>;
}

export const useInboxStore = create<InboxState>((set, get) => ({
  items:        [],
  unread_count: null,
  loading:      false,
  error:        null,
  mutationError: null,
  selectedId:   null,
  filter:       'all',

  fetch: async () => {
    const requestId = ++latestFetchRequest;
    set({ loading: true, error: null, unread_count: null });
    try {
      const { filter } = get();
      const data = await api.get<{ items: InboxItem[]; unread_count: number }>(
        `/api/inbox${filter !== 'all' ? `?filter=${filter}` : ''}`
      );
      if (requestId !== latestFetchRequest) return;
      if (!Array.isArray(data?.items) || !data.items.every(validInboxItem)
        || !Number.isSafeInteger(data.unread_count) || data.unread_count < 0) {
        throw new Error('Inbox response has incomplete records or an invalid unread count');
      }
      set({ items: data.items, unread_count: data.unread_count, error: null });
    } catch (err) {
      if (requestId === latestFetchRequest) {
        console.error('inbox fetch failed', err);
        set({ items: [], unread_count: null, selectedId: null, error: 'Inbox data could not be loaded. No empty-state conclusion is available.' });
      }
    } finally {
      if (requestId === latestFetchRequest) set({ loading: false });
    }
  },

  setFilter: (filter) => {
    // Entries from the previous filter are not evidence for the new view.
    set({ filter, items: [], selectedId: null, unread_count: null });
    get().fetch();
  },

  selectItem: (id) => {
    set({ selectedId: id });
    if (id) {
      const item = get().items.find((i) => i.id === id);
      if (item && !item.is_read) {
        get().markRead(id);
      }
    }
  },

  markRead: async (id) => {
    try {
      await api.patch(`/api/inbox/${encodeURIComponent(id)}/read`, {});
      set((s) => ({
        items: s.items.map((i) => (i.id === id ? { ...i, is_read: true } : i)),
        unread_count: s.unread_count === null ? null : Math.max(0, s.unread_count - (s.items.some((i) => i.id === id && !i.is_read) ? 1 : 0)),
        mutationError: null,
      }));
    } catch {
      set({ mutationError: 'Could not mark the item as read. It remains unread.' });
    }
  },
}));
