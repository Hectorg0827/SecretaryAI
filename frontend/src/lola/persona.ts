/**
 * Lola's client-side voice: short, warm, executive. Bilingual (EN/ES).
 *
 * Only UI chrome lives here. Lola's actual answers come from the backend
 * persona (see docs/lola/PERSONA.md, owned jointly with Workstream 1).
 */
import type { LolaLanguage, OrbState } from './contract';

const SPANISH_WORDS = new Set([
  'qué', 'que', 'cómo', 'como', 'cuánto', 'cuánta', 'cuántos', 'cuantos', 'dónde', 'donde',
  'está', 'esta', 'están', 'mis', 'mi', 'nuestras', 'nuestros', 'facturas', 'ventas', 'envío',
  'envio', 'contenedor', 'almacén', 'almacen', 'hola', 'gracias', 'negocio', 'hoy', 'cuentas',
  'pedido', 'el', 'la', 'los', 'las', 'del', 'por', 'para', 'con', 'pasando', 'necesito',
  'muéstrame', 'muestrame', 'quiero', 'llega', 'semana', 'bajaron', 'vencidas',
]);
const ENGLISH_WORDS = new Set([
  'the', 'what', 'how', 'my', 'our', 'is', 'are', 'show', 'where', 'when', 'which', 'why',
  'with', 'need', 'today', 'week', 'please', 'invoices', 'sales', 'container', 'any',
]);

/**
 * Lightweight language guess for switching UI copy and TTS voice. Whole-word
 * matching only (so "income" never counts as Spanish "como"). The backend
 * makes the final call and may emit a `language` event.
 */
export function detectLanguage(text: string, fallback: LolaLanguage): LolaLanguage {
  if (/[¿¡ñ]/i.test(text)) return 'es';
  const words = text.toLowerCase().match(/[\p{L}]+/gu) ?? [];
  if (words.length === 0) return fallback;
  let es = 0;
  let en = 0;
  for (const w of words) {
    if (SPANISH_WORDS.has(w)) es++;
    if (ENGLISH_WORDS.has(w)) en++;
  }
  if (es === en) return fallback;
  return es > en ? 'es' : 'en';
}

export function greeting(lang: LolaLanguage, name?: string | null, now = new Date()): string {
  const h = now.getHours();
  const who = name ? `, ${name.split(' ')[0]}` : '';
  if (lang === 'es') {
    const part = h < 12 ? 'Buenos días' : h < 19 ? 'Buenas tardes' : 'Buenas noches';
    return `${part}${who}.`;
  }
  const part = h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
  return `${part}${who}.`;
}

type Copy = Record<string, { en: string; es: string }>;

export const COPY = {
  askPlaceholder: { en: 'Ask Lola anything about the business…', es: 'Pregúntale a Lola sobre tu negocio…' },
  openLola: { en: 'Open Lola, your chief of staff', es: 'Abrir a Lola, tu jefa de gabinete' },
  closeLola: { en: 'Close Lola', es: 'Cerrar a Lola' },
  expand: { en: 'Open command center', es: 'Abrir centro de mando' },
  newConversation: { en: 'New conversation', es: 'Nueva conversación' },
  send: { en: 'Send', es: 'Enviar' },
  stop: { en: 'Stop', es: 'Detener' },
  micStart: { en: 'Start voice — Lola listens only while this is on', es: 'Activar voz — Lola solo escucha mientras esté activa' },
  micStop: { en: 'Stop listening', es: 'Dejar de escuchar' },
  attach: { en: 'Attach a document', es: 'Adjuntar un documento' },
  emptyTitle: { en: 'What would you like to know?', es: '¿Qué te gustaría saber?' },
  emptyBody: {
    en: 'Ask in plain words. I’ll pull the numbers, show the evidence, and suggest what to do next.',
    es: 'Pregunta con tus palabras. Traigo los datos, muestro la evidencia y te sugiero el siguiente paso.',
  },
  approve: { en: 'Approve & run', es: 'Aprobar y ejecutar' },
  reject: { en: 'Not now', es: 'Ahora no' },
  retry: { en: 'Try again', es: 'Intentar de nuevo' },
  demo: { en: 'Demo data', es: 'Datos de demostración' },
  settings: { en: 'Voice & privacy', es: 'Voz y privacidad' },
  history: { en: 'Saved conversations', es: 'Conversaciones guardadas' },
} satisfies Copy;

export function t(key: keyof typeof COPY, lang: LolaLanguage): string {
  return COPY[key][lang];
}

/** Short, honest status line for each orb state — also used for aria-live. */
export function orbStatusLabel(state: OrbState, lang: LolaLanguage, step?: string): string {
  const en: Record<OrbState, string> = {
    idle: 'Lola is ready',
    listening: 'Listening…',
    thinking: 'Thinking…',
    speaking: 'Speaking',
    working: step ? `Working: ${step}` : 'Working on it — not finished yet',
    awaiting_approval: 'Waiting for your approval',
    attention: 'Needs your attention',
    error: 'Connection problem — nothing was executed',
  };
  const es: Record<OrbState, string> = {
    idle: 'Lola está lista',
    listening: 'Escuchando…',
    thinking: 'Pensando…',
    speaking: 'Hablando',
    working: step ? `Trabajando: ${step}` : 'Trabajando — aún no termina',
    awaiting_approval: 'Esperando tu aprobación',
    attention: 'Requiere tu atención',
    error: 'Problema de conexión — no se ejecutó nada',
  };
  return (lang === 'es' ? es : en)[state];
}

export const STARTER_PROMPTS: Record<string, { en: string; es: string }[]> = {
  owner: [
    { en: 'What’s happening with my business?', es: '¿Qué está pasando con mi negocio?' },
    { en: 'Which invoices are overdue?', es: '¿Qué facturas están vencidas?' },
    { en: 'Why did sales drop last week?', es: '¿Por qué bajaron las ventas la semana pasada?' },
    { en: 'Where is container MSKU 448812?', es: '¿Dónde está el contenedor MSKU 448812?' },
  ],
  manager: [
    { en: 'What needs my attention today?', es: '¿Qué necesita mi atención hoy?' },
    { en: 'Which invoices are overdue?', es: '¿Qué facturas están vencidas?' },
    { en: 'Any logistics exceptions?', es: '¿Hay excepciones de logística?' },
    { en: 'What’s waiting for my approval?', es: '¿Qué espera mi aprobación?' },
  ],
  sales_rep: [
    { en: 'Which of my accounts need a call?', es: '¿Cuáles de mis cuentas necesitan una llamada?' },
    { en: 'When will Sunrise Distributors’ order arrive?', es: '¿Cuándo llega el pedido de Sunrise Distributors?' },
    { en: 'What’s running low this week?', es: '¿Qué se está agotando esta semana?' },
  ],
  back_office: [
    { en: 'Any receiving exceptions today?', es: '¿Hay excepciones de recepción hoy?' },
    { en: 'What’s arriving at the warehouse this week?', es: '¿Qué llega al almacén esta semana?' },
    { en: 'What’s running low this week?', es: '¿Qué se está agotando esta semana?' },
  ],
  viewer: [],
};
