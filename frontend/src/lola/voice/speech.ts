/**
 * Speech-to-text adapters.
 *
 * 1. Browser speech recognition (Chrome, Edge, Safari) — lowest latency,
 *    live interim transcript, natural end-of-turn detection on pauses.
 * 2. Fallback: record with MediaRecorder, stop on ~1.2 s of silence (or a
 *    tap), then POST once to the existing `/api/chat/transcribe` endpoint.
 *
 * Privacy: the mic is only opened by an explicit user action, audio is never
 * written to disk or storage on the client, and the recorder releases the
 * microphone as soon as a turn ends.
 */
import type { LolaLanguage } from '../contract';
import { API_BASE } from '../../lib/api';

export interface RecognizerHandlers {
  onInterim: (text: string) => void;
  onFinal: (text: string) => void;
  onEnd: () => void;
  onError: (kind: 'not-allowed' | 'network' | 'no-speech' | 'unsupported' | 'other', message: string) => void;
}

export interface Recognizer {
  start(): void;
  stop(): void;
  abort(): void;
  readonly engine: 'browser' | 'server';
}

const LOCALE: Record<LolaLanguage, string> = { en: 'en-US', es: 'es-US' };

type SR = {
  lang: string; interimResults: boolean; continuous: boolean; maxAlternatives: number;
  onresult: ((e: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void; stop(): void; abort(): void;
};

function browserCtor(): (new () => SR) | null {
  const w = window as unknown as { SpeechRecognition?: new () => SR; webkitSpeechRecognition?: new () => SR };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function voiceSupport(): { stt: 'browser' | 'server' | 'none'; tts: boolean } {
  const tts = typeof window !== 'undefined' && 'speechSynthesis' in window;
  if (browserCtor()) return { stt: 'browser', tts };
  const canRecord = typeof window !== 'undefined' && 'MediaRecorder' in window && !!navigator.mediaDevices?.getUserMedia;
  return { stt: canRecord ? 'server' : 'none', tts };
}

export function createRecognizer(lang: LolaLanguage, h: RecognizerHandlers): Recognizer {
  const Ctor = browserCtor();
  if (Ctor) return browserRecognizer(Ctor, lang, h);
  return serverRecognizer(lang, h);
}

function browserRecognizer(Ctor: new () => SR, lang: LolaLanguage, h: RecognizerHandlers): Recognizer {
  const rec = new Ctor();
  rec.lang = LOCALE[lang];
  rec.interimResults = true;
  rec.continuous = false; // one user turn; the browser ends it on a natural pause
  rec.maxAlternatives = 1;
  let finalText = '';
  let aborted = false;
  rec.onresult = (e) => {
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) finalText += r[0].transcript;
      else interim += r[0].transcript;
    }
    h.onInterim((finalText + interim).trim());
  };
  rec.onerror = (e) => {
    const map: Record<string, Parameters<RecognizerHandlers['onError']>[0]> = {
      'not-allowed': 'not-allowed', 'service-not-allowed': 'not-allowed', network: 'network', 'no-speech': 'no-speech', aborted: 'other',
    };
    if (e.error === 'aborted') return;
    h.onError(map[e.error] ?? 'other', e.error);
  };
  rec.onend = () => {
    const text = finalText.trim();
    if (text && !aborted) h.onFinal(text);
    h.onEnd();
  };
  return {
    engine: 'browser',
    start: () => rec.start(),
    stop: () => rec.stop(),
    abort: () => { aborted = true; rec.abort(); },
  };
}

function serverRecognizer(lang: LolaLanguage, h: RecognizerHandlers): Recognizer {
  let stream: MediaStream | null = null;
  let recorder: MediaRecorder | null = null;
  let ctx: AudioContext | null = null;
  let raf = 0;
  let aborted = false;
  const chunks: Blob[] = [];

  const release = () => {
    cancelAnimationFrame(raf);
    stream?.getTracks().forEach((t) => t.stop());
    ctx?.close().catch(() => undefined);
    stream = null; ctx = null;
  };

  return {
    engine: 'server',
    start: async () => {
      if (!navigator.mediaDevices?.getUserMedia) { h.onError('unsupported', 'No microphone API'); h.onEnd(); return; }
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      } catch {
        h.onError('not-allowed', 'Microphone permission denied'); h.onEnd(); return;
      }
      recorder = new MediaRecorder(stream);
      recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      recorder.onstop = async () => {
        release();
        if (aborted || chunks.length === 0) { h.onEnd(); return; }
        h.onInterim(lang === 'es' ? 'Transcribiendo…' : 'Transcribing…');
        try {
          const form = new FormData();
          form.append('audio', new Blob(chunks, { type: recorder?.mimeType || 'audio/webm' }), 'turn.webm');
          form.append('language', lang);
          const token = localStorage.getItem('secretary_token');
          const res = await fetch(`${API_BASE}/api/chat/transcribe`, { method: 'POST', body: form, headers: token ? { Authorization: `Bearer ${token}` } : {} });
          if (!res.ok) throw new Error(String(res.status));
          const { text } = (await res.json()) as { text?: string };
          if (text?.trim()) h.onFinal(text.trim()); else h.onError('no-speech', 'Nothing heard');
        } catch {
          h.onError('network', 'Transcription unavailable');
        } finally {
          chunks.length = 0; // never keep audio around
          h.onEnd();
        }
      };
      recorder.start();
      // Simple voice-activity detection: stop after ~1.2 s of quiet once speech began.
      try {
        ctx = new AudioContext();
        const src = ctx.createMediaStreamSource(stream);
        const an = ctx.createAnalyser();
        an.fftSize = 512;
        src.connect(an);
        const buf = new Uint8Array(an.fftSize);
        let spoke = false;
        let quietSince = 0;
        const started = performance.now();
        const tick = () => {
          an.getByteTimeDomainData(buf);
          let peak = 0;
          for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
          const now = performance.now();
          if (peak > 14) { spoke = true; quietSince = 0; } else if (spoke && !quietSince) quietSince = now;
          if ((spoke && quietSince && now - quietSince > 1200) || now - started > 30_000) {
            if (recorder?.state === 'recording') recorder.stop();
            return;
          }
          raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
      } catch { /* VAD optional; user can tap to stop */ }
    },
    stop: () => { if (recorder?.state === 'recording') recorder.stop(); else release(); },
    abort: () => { aborted = true; if (recorder?.state === 'recording') recorder.stop(); else release(); },
  };
}
