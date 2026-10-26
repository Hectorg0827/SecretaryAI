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

interface InboxState {
  items: InboxItem[];
  unread_count: number;
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
  unread_count: 0,
  loading:      false,
  error:        null,
  mutationError: null,
  selectedId:   null,
  filter:       'all',

  fetch: async () => {
    set({ loading: true, error: null });
    try {
      const { filter } = get();
      const data = await api.get<{ items: InboxItem[]; unread_count: number }>(
        `/api/inbox${filter !== 'all' ? `?filter=${filter}` : ''}`
      );
      set({ items: data.items, unread_count: data.unread_count, error: null });
    } catch (err) {
      console.error('inbox fetch failed', err);
      set({ items: [], unread_count: 0, selectedId: null, error: 'Inbox data could not be loaded. No empty-state conclusion is available.' });
    } finally {
      set({ loading: false });
    }
  },

  setFilter: (filter) => {
    set({ filter, selectedId: null });
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
        unread_count: Math.max(0, s.unread_count - (s.items.some((i) => i.id === id && !i.is_read) ? 1 : 0)),
        mutationError: null,
      }));
    } catch {
      set({ mutationError: 'Could not mark the item as read. It remains unread.' });
    }
  },
}));
