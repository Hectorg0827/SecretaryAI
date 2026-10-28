import { create } from 'zustand';
import { getToken, clearToken } from '../api/client';
import {
  login as apiLogin,
  loginWithTotp as apiLoginWithTotp,
  logout as apiLogout,
  getMe,
  UserProfile,
} from '../api/auth';

interface AuthState {
  user: UserProfile | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  // 2FA interim state
  needs2fa: boolean;
  preAuthToken: string | null;

  // Actions
  initialize: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  submitTotp: (code: string) => Promise<void>;
  cancel2fa: () => void;
  logout: () => Promise<void>;
  clearError: () => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  isAuthenticated: false,
  isLoading: true,
  error: null,
  needs2fa: false,
  preAuthToken: null,

  initialize: async () => {
    set({ isLoading: true });
    try {
      const token = await getToken();
      if (!token) {
        set({ isAuthenticated: false, isLoading: false });
        return;
      }
      const user = await getMe();
      set({ user, isAuthenticated: true, isLoading: false });
    } catch {
      await clearToken();
      set({ user: null, isAuthenticated: false, isLoading: false });
    }
  },

  login: async (email: string, password: string) => {
    set({ isLoading: true, error: null });
    try {
      const result = await apiLogin(email, password);
      if ('requires_2fa' in result && result.requires_2fa) {
        set({
          isLoading: false,
          needs2fa: true,
          preAuthToken: result.pre_auth_token,
          error: null,
        });
        return;
      }
      const user = await getMe();
      set({ user, isAuthenticated: true, isLoading: false, error: null });
    } catch (err) {
      // A failed profile read after token issuance is still a failed sign-in.
      // Do not leave an apparently signed-out device holding that JWT.
      let credentialClearFailed = false;
      try { await clearToken(); } catch { credentialClearFailed = true; }
      const failure = credentialClearFailed
        ? new Error('Could not clear the incomplete session from secure storage. Contact your administrator.')
        : err;
      set({
        isLoading: false,
        error: failure instanceof Error ? failure.message : 'Login failed',
      });
      throw failure;
    }
  },

  submitTotp: async (code: string) => {
    const { preAuthToken } = get();
    if (!preAuthToken) {
      const error = new Error('Your sign-in challenge expired. Start again.');
      set({ needs2fa: false, error: error.message });
      throw error;
    }
    set({ isLoading: true, error: null });
    try {
      await apiLoginWithTotp(preAuthToken, code);
      const user = await getMe();
      set({
        user,
        isAuthenticated: true,
        isLoading: false,
        needs2fa: false,
        preAuthToken: null,
        error: null,
      });
    } catch (err) {
      let credentialClearFailed = false;
      try { await clearToken(); } catch { credentialClearFailed = true; }
      const failure = credentialClearFailed
        ? new Error('Could not clear the incomplete session from secure storage. Contact your administrator.')
        : err;
      set({
        isLoading: false,
        error: failure instanceof Error ? failure.message : 'Invalid 2FA code',
      });
      throw failure;
    }
  },

  cancel2fa: () => set({ needs2fa: false, preAuthToken: null, error: null }),

  logout: async () => {
    await apiLogout();
    set({ user: null, isAuthenticated: false, needs2fa: false, preAuthToken: null });
  },

  clearError: () => set({ error: null }),
}));
