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
  selectedId:   null,
  filter:       'all',

  fetch: async () => {
    set({ loading: true });
    try {
      const { filter } = get();
      const data = await api.get<{ items: InboxItem[]; unread_count: number }>(
        `/api/inbox${filter !== 'all' ? `?filter=${filter}` : ''}`
      );
      set({ items: data.items, unread_count: data.unread_count });
    } catch (err) {
      console.error('inbox fetch failed', err);
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
    set((s) => ({
      items: s.items.map((i) => (i.id === id ? { ...i, is_read: true } : i)),
      unread_count: Math.max(0, s.unread_count - 1),
    }));
    try {
      await api.patch(`/api/inbox/${encodeURIComponent(id)}/read`, {});
    } catch {
      // best-effort — local state is already updated
    }
  },
}));
