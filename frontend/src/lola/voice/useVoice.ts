/**
 * Voice conversation controller.
 *
 * Turn-taking:  tap mic → listening (live transcript) → natural pause ends the
 *               turn → Lola thinks/works → speaks the answer (if enabled).
 * Barge-in:     tapping the mic (or pressing Esc to stop) while Lola speaks
 *               stops her instantly. A new utterance while she is still
 *               working supersedes that turn (correction).
 * Default:      never always-on. `continuousConversation` re-opens the mic
 *               after she finishes only if the user turned it on.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { createRecognizer, voiceSupport, type Recognizer } from './speech';
import { createSpeaker } from './tts';
import { onReceipt, onTurnComplete, useLolaStore } from '../store';
import { useLolaPrefs } from '../settings';
import { detectLanguage } from '../persona';

const speaker = createSpeaker();

/** Module-level so several mounted composers never speak the same answer twice. */
const voiceSession = { lastWasVoice: false, restart: null as null | (() => void) };
let listenersInstalled = false;

function installSpeechListeners() {
  if (listenersInstalled) return;
  listenersInstalled = true;
  const setVoiceState = (v: 'off' | 'speaking') => useLolaStore.getState().setVoiceState(v);
  // Speak finished voice-initiated answers.
  onTurnComplete((m) => {
    const prefs = useLolaPrefs.getState();
    if (!voiceSession.lastWasVoice || m.channel !== 'voice' || !prefs.speakReplies || !m.text) return;
    speaker.speak(m.text, m.language,
      () => setVoiceState('speaking'),
      () => {
        setVoiceState('off');
        if (useLolaPrefs.getState().continuousConversation && voiceSession.lastWasVoice) voiceSession.restart?.();
      });
  });
  // Receipts: speak the SERVER's message — verified or not — so the spoken
  // word never claims more than the receipt does.
  onReceipt((r, l) => {
    if (!voiceSession.lastWasVoice || !useLolaPrefs.getState().speakReplies) return;
    speaker.speak(r.message, l, () => setVoiceState('speaking'), () => setVoiceState('off'));
  });
}

export function stopSpeaking() {
  speaker.cancel();
  useLolaStore.getState().setVoiceState('off');
}

export function useVoice(route?: string) {
  const [interim, setInterim] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const rec = useRef<Recognizer | null>(null);
  const support = useRef(voiceSupport());
  const lang = useLolaStore((s) => s.language);
  const setVoiceState = useLolaStore((s) => s.setVoiceState);
  const routeRef = useRef(route);
  routeRef.current = route;

  const stopListening = useCallback(() => {
    rec.current?.stop();
  }, []);

  const start = useCallback(() => {
    setError(null);
    // Barge-in: the user always wins.
    if (speaker.speaking) speaker.cancel();
    if (support.current.stt === 'none') {
      setError(lang === 'es' ? 'Este navegador no permite voz. Puedes escribir.' : 'Voice isn’t available in this browser. You can type instead.');
      return;
    }
    rec.current?.abort();
    const pref = useLolaPrefs.getState().language;
    const l = pref === 'auto' ? lang : pref;
    rec.current = createRecognizer(l, {
      onInterim: (t) => setInterim(t),
      onFinal: (t) => {
        setInterim(t);
        voiceSession.lastWasVoice = true;
        const detected = pref === 'auto' ? detectLanguage(t, l) : l;
        if (detected !== l) useLolaStore.getState().setLanguage(detected);
        void useLolaStore.getState().send(t, { channel: 'voice', route: routeRef.current });
      },
      onEnd: () => {
        setListening(false);
        if (useLolaStore.getState().voiceState === 'listening') setVoiceState('off');
        setTimeout(() => setInterim(''), 1200);
      },
      onError: (kind) => {
        const es = l === 'es';
        const msg = {
          'not-allowed': es ? 'Necesito permiso para usar el micrófono.' : 'I need microphone permission to listen.',
          network: es ? 'Se perdió la conexión de voz. Puedes escribir mientras tanto.' : 'Voice connection dropped. You can type in the meantime.',
          'no-speech': es ? 'No escuché nada. Toca el micrófono para intentar de nuevo.' : 'I didn’t catch anything. Tap the mic to try again.',
          unsupported: es ? 'Voz no disponible en este dispositivo.' : 'Voice isn’t available on this device.',
          other: es ? 'La voz tuvo un problema. Intenta de nuevo.' : 'Voice hit a snag. Please try again.',
        }[kind];
        setError(msg);
      },
    });
    setListening(true);
    setVoiceState('listening');
    rec.current.start();
  }, [lang, setVoiceState]);

  const toggle = useCallback(() => {
    if (listening) stopListening(); else start();
  }, [listening, start, stopListening]);

  useEffect(() => {
    installSpeechListeners();
    voiceSession.restart = start;
    return () => { if (voiceSession.restart === start) voiceSession.restart = null; };
  }, [start]);

  useEffect(() => () => { rec.current?.abort(); }, []);

  /** Typing a message ends the "voice conversation" mode. */
  const markTyped = useCallback(() => { voiceSession.lastWasVoice = false; }, []);

  return { supported: support.current, listening, interim, error, start, stop: stopListening, toggle, markTyped, clearError: () => setError(null) };
}
