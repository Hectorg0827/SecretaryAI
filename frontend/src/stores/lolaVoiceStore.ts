import { create } from 'zustand';

type VoiceStatus = 'idle' | 'speaking' | 'error';

interface LolaVoiceState {
  status: VoiceStatus;
  outputLevel: number;
  speak: (text: string, options?: { rate?: number }) => boolean;
  stop: () => void;
}

let currentUtterance: SpeechSynthesisUtterance | null = null;
let boundaryReset: ReturnType<typeof setTimeout> | null = null;

const cancelBoundaryReset = () => {
  if (boundaryReset !== null) clearTimeout(boundaryReset);
  boundaryReset = null;
};

/**
 * Browser speech synthesis does not expose raw PCM samples to Web Audio.
 * 'boundary' represents actual spoken word boundaries (cadence), not invented
 * sound amplitude. Microphone input uses real RMS analysis in useMicLevel.
 */
export const useLolaVoiceStore = create<LolaVoiceState>((set) => ({
  status: 'idle',
  outputLevel: 0,

  stop: () => {
    currentUtterance = null;
    cancelBoundaryReset();
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    set({ status: 'idle', outputLevel: 0 });
  },

  speak: (text, options) => {
    if (typeof window === 'undefined' ||
        !('speechSynthesis' in window) ||
        typeof SpeechSynthesisUtterance === 'undefined' ||
        !text.trim()) return false;

    currentUtterance = null;
    cancelBoundaryReset();
    window.speechSynthesis.cancel();
    try {
      const utterance = new SpeechSynthesisUtterance(text);
      currentUtterance = utterance;
      utterance.lang = 'en-US';
      utterance.rate = options?.rate ?? 0.97;
      utterance.onstart = () => {
        if (currentUtterance === utterance) set({ status: 'speaking', outputLevel: 0.22 });
      };
      utterance.onboundary = () => {
        if (currentUtterance !== utterance) return;
        set({ status: 'speaking', outputLevel: 0.85 });
        cancelBoundaryReset();
        boundaryReset = setTimeout(() => {
          if (currentUtterance === utterance) set({ outputLevel: 0.18 });
        }, 160);
      };
      const settle = () => {
        if (currentUtterance !== utterance) return;
        currentUtterance = null;
        cancelBoundaryReset();
        set({ status: 'idle', outputLevel: 0 });
      };
      utterance.onend = settle;
      utterance.onerror = () => {
        if (currentUtterance !== utterance) return;
        currentUtterance = null;
        cancelBoundaryReset();
        set({ status: 'error', outputLevel: 0 });
      };
      window.speechSynthesis.speak(utterance);
      return true;
    } catch {
      currentUtterance = null;
      cancelBoundaryReset();
      set({ status: 'error', outputLevel: 0 });
      return false;
    }
  },
}));
