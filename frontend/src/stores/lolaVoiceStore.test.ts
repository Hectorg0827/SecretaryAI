// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useLolaVoiceStore } from './lolaVoiceStore';

type MockUtterance = {
  text: string;
  lang?: string;
  rate?: number;
  onstart?: () => void;
  onboundary?: () => void;
  onend?: () => void;
  onerror?: () => void;
};

const utterances: MockUtterance[] = [];
const speech = { cancel: vi.fn(), speak: vi.fn() };

beforeEach(() => {
  vi.clearAllMocks();
  utterances.length = 0;
  class SyntheticUtterance implements MockUtterance {
    lang = '';
    rate = 1;
    onstart?: () => void;
    onboundary?: () => void;
    onend?: () => void;
    onerror?: () => void;
    constructor(public text: string) {
      utterances.push(this);
    }
  }
  vi.stubGlobal('SpeechSynthesisUtterance', SyntheticUtterance);
  Object.defineProperty(window, 'speechSynthesis', {
    configurable: true, value: speech,
  });
  useLolaVoiceStore.getState().stop();
});

afterEach(() => {
  useLolaVoiceStore.getState().stop();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('Lola voice state reflects actual browser speech callbacks', () => {
  it('does not start speech or pretend to be speaking before the browser onstart event', () => {
    expect(useLolaVoiceStore.getState().speak('Business briefing')).toBe(true);
    expect(speech.speak).toHaveBeenCalledOnce();
    expect(useLolaVoiceStore.getState().status).toBe('idle');
    expect(utterances[utterances.length - 1]).not.toBeNull();
    const utterance = utterances[utterances.length - 1] as MockUtterance | null;
    utterance?.onstart?.();
    expect(useLolaVoiceStore.getState().status).toBe('speaking');
    expect(useLolaVoiceStore.getState().outputLevel).toBe(0.22);
  });

  it('reacts to actual word boundaries and returns idle after speech ends', () => {
    vi.useFakeTimers();
    useLolaVoiceStore.getState().speak('Next priority');
    const utterance = utterances[utterances.length - 1] as MockUtterance | null;
    utterance?.onstart?.();
    utterance?.onboundary?.();
    expect(useLolaVoiceStore.getState().outputLevel).toBe(0.85);
    vi.advanceTimersByTime(165);
    expect(useLolaVoiceStore.getState().outputLevel).toBe(0.18);
    utterance?.onend?.();
    expect(useLolaVoiceStore.getState().status).toBe('idle');
    expect(useLolaVoiceStore.getState().outputLevel).toBe(0);
  });

  it('stops speaking and cancels browser audio on explicit stop', () => {
    useLolaVoiceStore.getState().speak('Hello');
    const utterance = utterances[utterances.length - 1] as MockUtterance | null;
    utterance?.onstart?.();
    useLolaVoiceStore.getState().stop();
    expect(speech.cancel).toHaveBeenCalled();
    expect(useLolaVoiceStore.getState().status).toBe('idle');
    utterance?.onboundary?.();
    expect(useLolaVoiceStore.getState().outputLevel).toBe(0);
  });
});
