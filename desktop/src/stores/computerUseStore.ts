/**
 * Computer Use store — manages the state of active screen-reading sessions.
 * The AI can only capture the screen during an approved, active session.
 */
import { create } from 'zustand';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

export type CUStatus = 'idle' | 'running' | 'waiting_approval' | 'error';

interface CUSession {
  sessionId: string;
  appName: string;
  task: string;
  startedAt: Date;
  stepsCompleted: number;
}

interface ComputerUseStore {
  status: CUStatus;
  currentSession: CUSession | null;
  lastError: string | null;
  pendingApprovals: CUApproval[];

  // Actions
  startSession: (appName: string, task: string) => Promise<void>;
  approveAction: (approvalId: string) => Promise<void>;
  rejectAction: (approvalId: string) => Promise<void>;
  captureScreen: () => Promise<string>; // returns base64 PNG
}

interface CUApproval {
  id: string;
  actionType: string;
  description: string;
  appName: string;
  screenshotB64?: string;
  createdAt: Date;
}

export const useComputerUseStore = create<ComputerUseStore>((set, get) => ({
  status: 'idle',
  currentSession: null,
  lastError: null,
  pendingApprovals: [],

  startSession: async (appName: string, task: string) => {
    set({ status: 'running', lastError: null });
    try {
      // TODO: POST to backend /api/computer-use/start
      // Backend orchestrates the CU loop and calls back via WebSocket
      const sessionId = crypto.randomUUID();
      set({
        currentSession: {
          sessionId,
          appName,
          task,
          startedAt: new Date(),
          stepsCompleted: 0,
        },
      });
    } catch (error) {
      set({ status: 'error', lastError: String(error) });
    }
  },

  captureScreen: async (): Promise<string> => {
    // Only allowed during an active session
    if (get().status !== 'running') {
      throw new Error('Screen capture only allowed during an active Computer Use session');
    }
    return await invoke<string>('capture_screen');
  },

  approveAction: async (approvalId: string) => {
    set((s) => ({
      pendingApprovals: s.pendingApprovals.filter((a) => a.id !== approvalId),
    }));
    // TODO: POST approval to backend
  },

  rejectAction: async (approvalId: string) => {
    set((s) => ({
      pendingApprovals: s.pendingApprovals.filter((a) => a.id !== approvalId),
      status: 'idle',
      currentSession: null,
    }));
    // TODO: POST rejection to backend
  },
}));
