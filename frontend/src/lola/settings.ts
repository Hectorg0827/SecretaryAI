/**
 * Per-user Lola preferences (this device). Stored in localStorage as a
 * convenience only; every read/write is guarded so private mode still works.
 */
import { create } from 'zustand';
import type { AudioRetention, LolaLanguage, TranscriptRetention } from './contract';

export interface LolaPrefs {
  /** 'auto' follows what the user speaks/types. */
  language: 'auto' | LolaLanguage;
  /** Read replies aloud after a voice question. */
  speakReplies: boolean;
  /** Keep the mic open for a follow-up after Lola finishes speaking. Off by default. */
  continuousConversation: boolean;
  transcriptRetention: TranscriptRetention;
  /** Raw audio: never kept by default. */
  audioRetention: AudioRetention;
}

export const DEFAULT_PREFS: LolaPrefs = {
  language: 'auto',
  speakReplies: true,
  continuousConversation: false,
  transcriptRetention: 'session',
  audioRetention: 'none',
};

const KEY = 'lola_prefs_v1';

function load(): LolaPrefs {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return DEFAULT_PREFS;
    return { ...DEFAULT_PREFS, ...(JSON.parse(raw) as Partial<LolaPrefs>) };
  } catch {
    return DEFAULT_PREFS;
  }
}

interface PrefsStore extends LolaPrefs {
  update: (patch: Partial<LolaPrefs>) => void;
}

export const useLolaPrefs = create<PrefsStore>((set, get) => ({
  ...load(),
  update: (patch) => {
    set(patch);
    try {
      const { update: _u, ...rest } = { ...get(), ...patch };
      void _u;
      window.localStorage.setItem(KEY, JSON.stringify(rest));
    } catch { /* storage unavailable — keep in memory */ }
  },
}));
