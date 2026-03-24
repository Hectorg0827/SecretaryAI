/**
 * Computer Use store — manages the state of active screen-reading sessions.
 * The AI can only capture the screen during an approved, active session.
 */
import { create } from 'zustand';
import { invoke } from '@tauri-apps/api/core';

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
  pollSession: () => Promise<void>;
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

const API =
  typeof window !== 'undefined' && (window as any).__SECRETARY_API__
    ? (window as any).__SECRETARY_API__
    : import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

function getToken(): string | null {
  return localStorage.getItem('secretary_token');
}

async function apiFetch<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = getToken();
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    throw new Error(err.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export const useComputerUseStore = create<ComputerUseStore>((set, get) => ({
  status: 'idle',
  currentSession: null,
  lastError: null,
  pendingApprovals: [],

  startSession: async (appName: string, task: string) => {
    set({ status: 'running', lastError: null });
    try {
      const data = await apiFetch<{ session_id: string; status: string }>(
        'POST',
        '/api/computer-use/start',
        { app_name: appName, task },
      );
      set({
        currentSession: {
          sessionId: data.session_id,
          appName,
          task,
          startedAt: new Date(),
          stepsCompleted: 0,
        },
      });
    } catch (error) {
      set({ status: 'error', lastError: String(error), currentSession: null });
    }
  },

  pollSession: async () => {
    const session = get().currentSession;
    if (!session) return;
    try {
      const data = await apiFetch<{
        session_id: string;
        status: string;
        steps_completed: number;
        result?: unknown;
        error?: string;
        pending_approval?: { id: string; action_type: string; description: string; created_at: string } | null;
      }>('GET', `/api/computer-use/sessions/${session.sessionId}`);

      set((s) => ({
        status: data.status as CUStatus,
        currentSession: s.currentSession
          ? { ...s.currentSession, stepsCompleted: data.steps_completed }
          : null,
        lastError: data.error ?? null,
        pendingApprovals: data.pending_approval
          ? [
              {
                id: data.pending_approval.id,
                actionType: data.pending_approval.action_type,
                description: data.pending_approval.description,
                appName: session.appName,
                createdAt: new Date(data.pending_approval.created_at),
              },
            ]
          : [],
      }));

      if (data.status === 'done' || data.status === 'error') {
        if (data.status === 'done') {
          // Small delay so UI can show done state, then reset
          setTimeout(() => set({ status: 'idle', currentSession: null, pendingApprovals: [] }), 3000);
        }
      }
    } catch (error) {
      // Transient poll failure — ignore
    }
  },

  captureScreen: async (): Promise<string> => {
    if (get().status !== 'running') {
      throw new Error('Screen capture only allowed during an active Computer Use session');
    }
    return await invoke<string>('capture_screen');
  },

  approveAction: async (approvalId: string) => {
    const session = get().currentSession;
    if (!session) return;
    try {
      await apiFetch('POST', '/api/computer-use/approve', {
        session_id: session.sessionId,
        approval_id: approvalId,
      });
      set((s) => ({
        pendingApprovals: s.pendingApprovals.filter((a) => a.id !== approvalId),
        status: 'running',
      }));
    } catch (error) {
      set({ lastError: String(error) });
    }
  },

  rejectAction: async (approvalId: string) => {
    const session = get().currentSession;
    if (!session) return;
    try {
      await apiFetch('POST', '/api/computer-use/reject', {
        session_id: session.sessionId,
        approval_id: approvalId,
      });
      set((s) => ({
        pendingApprovals: s.pendingApprovals.filter((a) => a.id !== approvalId),
        status: 'idle',
        currentSession: null,
      }));
    } catch (error) {
      set({ lastError: String(error), status: 'idle', currentSession: null });
    }
  },
}));
