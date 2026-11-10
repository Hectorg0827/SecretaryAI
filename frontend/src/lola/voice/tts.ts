/**
 * Text-to-speech with the platform voice. Speaks only text the server sent
 * (answers, verified receipts) — never a client-made completion claim.
 */
import type { LolaLanguage } from '../contract';

const LOCALE: Record<LolaLanguage, string> = { en: 'en-US', es: 'es-US' };

/** Remove markup and symbols that sound robotic when read aloud. */
export function speakable(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/[*_#>`~|]/g, '')
    .replace(/\[(.*?)\]\(.*?\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
}

function pickVoice(lang: LolaLanguage): SpeechSynthesisVoice | undefined {
  const voices = window.speechSynthesis.getVoices();
  const want = LOCALE[lang].toLowerCase();
  const prefix = lang;
  const score = (v: SpeechSynthesisVoice) =>
    (v.lang.toLowerCase() === want ? 4 : v.lang.toLowerCase().startsWith(prefix) ? 2 : 0)
    + (/natural|neural|premium|enhanced|google|samantha|paulina|mónica|monica/i.test(v.name) ? 1 : 0);
  return voices.filter((v) => v.lang.toLowerCase().startsWith(prefix)).sort((a, b) => score(b) - score(a))[0];
}

export interface Speaker {
  speak(text: string, lang: LolaLanguage, onStart?: () => void, onEnd?: () => void): void;
  cancel(): void;
  readonly speaking: boolean;
}

export function createSpeaker(): Speaker {
  let active = false;
  // Checked at call time: some browsers expose speechSynthesis late.
  const isSupported = () => typeof window !== 'undefined' && 'speechSynthesis' in window;
  return {
    get speaking() { return active; },
    speak(text, lang, onStart, onEnd) {
      const clean = speakable(text);
      if (!isSupported() || !clean) { onEnd?.(); return; }
      window.speechSynthesis.cancel();
      // Sentence chunks start sooner and make barge-in feel instant.
      const parts = clean.match(/[^.!?¡¿]+[.!?]+|\S[^.!?]*$/g) ?? [clean];
      const voice = pickVoice(lang);
      active = true;
      parts.forEach((p, i) => {
        const u = new SpeechSynthesisUtterance(p.trim());
        u.lang = LOCALE[lang];
        if (voice) u.voice = voice;
        u.rate = 1.03;
        u.pitch = 1;
        if (i === 0) u.onstart = () => onStart?.();
        if (i === parts.length - 1) {
          u.onend = () => { active = false; onEnd?.(); };
          u.onerror = () => { active = false; onEnd?.(); };
        }
        window.speechSynthesis.speak(u);
      });
    },
    cancel() {
      if (isSupported()) window.speechSynthesis.cancel();
      active = false;
    },
  };
}
