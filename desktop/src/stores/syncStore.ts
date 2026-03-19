import { create } from 'zustand';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

interface SyncStatus {
  lastSyncAt: string | null;
  status: 'idle' | 'syncing' | 'error';
  message: string | null;
}

interface SyncStore {
  syncStatus: SyncStatus;
  triggerSync: () => Promise<void>;
  listenForSyncEvents: () => Promise<() => void>;
}

export const useSyncStore = create<SyncStore>((set) => ({
  syncStatus: { lastSyncAt: null, status: 'idle', message: null },

  triggerSync: async () => {
    set((s) => ({
      syncStatus: { ...s.syncStatus, status: 'syncing', message: 'Syncing...' },
    }));
    try {
      const result = await invoke<SyncStatus>('trigger_sync');
      set({ syncStatus: result });
    } catch (error) {
      set({
        syncStatus: {
          lastSyncAt: null,
          status: 'error',
          message: String(error),
        },
      });
    }
  },

  listenForSyncEvents: async () => {
    const unlistenStarted = await listen<SyncStatus>('sync-started', (event) => {
      set({ syncStatus: event.payload });
    });
    const unlistenCompleted = await listen<SyncStatus>('sync-completed', (event) => {
      set({ syncStatus: event.payload });
    });
    const unlistenError = await listen<SyncStatus>('sync-error', (event) => {
      set({ syncStatus: event.payload });
    });

    return () => {
      unlistenStarted();
      unlistenCompleted();
      unlistenError();
    };
  },
}));
