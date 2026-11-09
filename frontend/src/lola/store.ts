/**
 * Lola conversation state. One store for the orb, the panel and the command
 * center so the experience is continuous across views and routes.
 *
 * Honesty invariants enforced here (covered by tests):
 *  - A turn is only `complete` when the server sent `done`.
 *  - An action is only `executed` when a receipt is `executed` AND `verified`.
 *  - Errors and interruptions are recorded, never silently dropped.
 */
import { create } from 'zustand';
import type {
  ActionPreview, Attachment, EvidenceCard, LolaLanguage, Notice, OrbState, Receipt,
} from './contract';
import type { LolaTransport } from './transport';
import { mockEnabled } from './transport';
import { HttpLolaTransport } from './httpTransport';
import { MockLolaTransport } from './mockTransport';
import { detectLanguage } from './persona';
import { guardAction, guardCard } from './permissions';
import { currentRole } from '../hooks/useAuth';
import { useLolaPrefs } from './settings';

export type PanelMode = 'closed' | 'panel' | 'full';
export type TurnStatus = 'streaming' | 'complete' | 'interrupted' | 'error';

export interface LolaMessage {
  id: string;
  role: 'user' | 'lola';
  text: string;
  language: LolaLanguage;
  channel: 'text' | 'voice';
  status: TurnStatus;
  cards: EvidenceCard[];
  actions: ActionPreview[];
  receipts: Receipt[];
  notices: Notice[];
  suggestions: string[];
  attachments?: Attachment[];
  error?: { message: string; retryable: boolean };
  /** Latency instrumentation (ms since epoch). */
  startedAt: number;
  firstEventAt?: number;
  firstTextAt?: number;
  completedAt?: number;
  /** For retries: the user message that produced this reply. */
  replyTo?: string;
}

export interface TurnMetric {
  id: string;
  channel: 'text' | 'voice';
  timeToFirstEventMs?: number;
  timeToFirstTextMs?: number;
  totalMs?: number;
  status: TurnStatus;
}

interface LolaState {
  mode: PanelMode;
  messages: LolaMessage[];
  conversationId: string | null;
  language: LolaLanguage;
  /** Set by the voice layer while listening / speaking. */
  voiceState: 'off' | 'listening' | 'speaking';
  workingStep?: string;
  streamingId: string | null;
  metrics: TurnMetric[];
  transportKind: 'http' | 'mock';

  open: (mode?: Exclude<PanelMode, 'closed'>) => void;
  close: () => void;
  send: (text: string, opts?: { channel?: 'text' | 'voice'; attachments?: Attachment[]; route?: string }) => Promise<void>;
  interrupt: () => void;
  retry: (lolaMessageId: string) => Promise<void>;
  approve: (messageId: string, actionId: string) => Promise<void>;
  reject: (messageId: string, actionId: string) => Promise<void>;
  newConversation: () => void;
  setLanguage: (l: LolaLanguage) => void;
  /** Load a saved conversation (text turns only; evidence is re-fetched on follow-up). */
  loadConversation: (conversationId: string, rows: { role: string; content: string; created_at?: string }[]) => void;
  setVoiceState: (v: LolaState['voiceState']) => void;
}

let transport: LolaTransport | null = null;
export function getTransport(): LolaTransport {
  if (!transport) transport = mockEnabled() ? new MockLolaTransport() : new HttpLolaTransport();
  return transport;
}
/** Tests / demos may inject a transport. */
export function setTransport(t: LolaTransport) {
  transport = t;
  useLolaStore.setState({ transportKind: t.kind });
}

let controller: AbortController | null = null;
let seq = 0;
const nextId = (p: string) => `${p}-${Date.now().toString(36)}-${++seq}`;

/** Turn listeners (used by the voice layer to speak finished answers). */
type TurnListener = (m: LolaMessage) => void;
const turnListeners = new Set<TurnListener>();
export function onTurnComplete(fn: TurnListener): () => void {
  turnListeners.add(fn);
  return () => turnListeners.delete(fn);
}
/** Receipt listeners — voice speaks ONLY verified receipts. */
type ReceiptListener = (r: Receipt, lang: LolaLanguage) => void;
const receiptListeners = new Set<ReceiptListener>();
export function onReceipt(fn: ReceiptListener): () => void {
  receiptListeners.add(fn);
  return () => receiptListeners.delete(fn);
}

function initialLanguage(): LolaLanguage {
  const pref = useLolaPrefs.getState().language;
  if (pref !== 'auto') return pref;
  try {
    return navigator.language?.toLowerCase().startsWith('es') ? 'es' : 'en';
  } catch {
    return 'en';
  }
}

export const useLolaStore = create<LolaState>((set, get) => {
  const patchMsg = (id: string, fn: (m: LolaMessage) => LolaMessage) =>
    set((s) => ({ messages: s.messages.map((m) => (m.id === id ? fn(m) : m)) }));

  const recordMetric = (m: LolaMessage) =>
    set((s) => ({
      metrics: [
        ...s.metrics.slice(-49),
        {
          id: m.id,
          channel: m.channel,
          status: m.status,
          timeToFirstEventMs: m.firstEventAt ? m.firstEventAt - m.startedAt : undefined,
          timeToFirstTextMs: m.firstTextAt ? m.firstTextAt - m.startedAt : undefined,
          totalMs: m.completedAt ? m.completedAt - m.startedAt : undefined,
        },
      ],
    }));

  async function runTurn(text: string, channel: 'text' | 'voice', attachments: Attachment[] | undefined, route: string | undefined, supersedes: boolean) {
    const prefs = useLolaPrefs.getState();
    const lang = prefs.language === 'auto' ? detectLanguage(text, get().language) : prefs.language;
    const role = currentRole();
    const userMsg: LolaMessage = {
      id: nextId('u'), role: 'user', text, language: lang, channel, status: 'complete',
      cards: [], actions: [], receipts: [], notices: [], suggestions: [], attachments, startedAt: Date.now(),
    };
    const reply: LolaMessage = {
      id: nextId('l'), role: 'lola', text: '', language: lang, channel, status: 'streaming',
      cards: [], actions: [], receipts: [], notices: [], suggestions: [], startedAt: Date.now(), replyTo: userMsg.id,
    };
    set((s) => ({ messages: [...s.messages, userMsg, reply], language: lang, streamingId: reply.id, workingStep: undefined }));

    const ctrl = new AbortController();
    controller = ctrl;
    let sawDone = false;
    try {
      const stream = getTransport().turn({
        message: text,
        conversation_id: get().conversationId ?? undefined,
        language: lang,
        channel,
        supersedes_turn: supersedes || undefined,
        attachments,
        context_route: route,
        retention: { transcript: prefs.transcriptRetention, audio: prefs.audioRetention },
      }, ctrl.signal);

      for await (const ev of stream) {
        if (ctrl.signal.aborted) break;
        const now = Date.now();
        patchMsg(reply.id, (m) => ({ ...m, firstEventAt: m.firstEventAt ?? now }));
        switch (ev.type) {
          case 'conversation':
            set({ conversationId: ev.conversation_id });
            break;
          case 'status':
            set({ workingStep: ev.state === 'working' ? ev.step : undefined });
            break;
          case 'language':
            set({ language: ev.language });
            patchMsg(reply.id, (m) => ({ ...m, language: ev.language }));
            break;
          case 'text':
            set({ workingStep: undefined });
            patchMsg(reply.id, (m) => ({ ...m, text: m.text + ev.delta, firstTextAt: m.firstTextAt ?? now }));
            break;
          case 'card':
            patchMsg(reply.id, (m) => ({ ...m, cards: [...m.cards, guardCard(ev.card, role)] }));
            break;
          case 'action_preview':
            patchMsg(reply.id, (m) => ({ ...m, actions: [...m.actions, guardAction(ev.action, role)] }));
            break;
          case 'receipt':
            patchMsg(reply.id, (m) => ({ ...m, receipts: [...m.receipts, ev.receipt] }));
            break;
          case 'notice':
            patchMsg(reply.id, (m) => ({ ...m, notices: [...m.notices, ev.notice] }));
            break;
          case 'suggestions':
            patchMsg(reply.id, (m) => ({ ...m, suggestions: ev.items.slice(0, 4) }));
            break;
          case 'error':
            patchMsg(reply.id, (m) => ({ ...m, status: 'error', error: { message: ev.message, retryable: ev.retryable } }));
            break;
          case 'done':
            sawDone = true;
            break;
        }
      }
    } catch {
      if (!ctrl.signal.aborted) {
        patchMsg(reply.id, (m) => ({ ...m, status: 'error', error: { message: 'Something went wrong on my side. Nothing was executed.', retryable: true } }));
      }
    } finally {
      const now = Date.now();
      patchMsg(reply.id, (m) => {
        if (ctrl.signal.aborted) return { ...m, status: 'interrupted', completedAt: now };
        if (m.status === 'error') return { ...m, completedAt: now };
        if (!sawDone) {
          return { ...m, status: 'error', completedAt: now, error: { message: 'I didn’t get a complete answer. Nothing was executed.', retryable: true } };
        }
        return { ...m, status: 'complete', completedAt: now };
      });
      if (controller === ctrl) controller = null;
      if (get().streamingId === reply.id) set({ streamingId: null, workingStep: undefined });
      const final = get().messages.find((m) => m.id === reply.id);
      if (final) {
        recordMetric(final);
        if (final.status === 'complete') turnListeners.forEach((fn) => { try { fn(final); } catch { /* a speaker failure must never break a turn */ } });
      }
    }
  }

  return {
    mode: 'closed',
    messages: [],
    conversationId: null,
    language: initialLanguage(),
    voiceState: 'off',
    streamingId: null,
    metrics: [],
    transportKind: mockEnabled() ? 'mock' : 'http',

    open: (mode = 'panel') => set({ mode }),
    close: () => {
      set({ mode: 'closed' });
      if (useLolaPrefs.getState().transcriptRetention === 'none' && !get().streamingId) {
        set({ messages: [], conversationId: null });
      }
    },

    send: async (text, opts = {}) => {
      const clean = text.trim();
      if (!clean) return;
      const supersedes = !!get().streamingId;
      if (supersedes) get().interrupt();
      await runTurn(clean, opts.channel ?? 'text', opts.attachments, opts.route, supersedes);
    },

    interrupt: () => {
      controller?.abort();
      controller = null;
      const id = get().streamingId;
      if (id) {
        patchMsg(id, (m) => ({ ...m, status: 'interrupted', completedAt: Date.now() }));
        set({ streamingId: null, workingStep: undefined });
      }
    },

    retry: async (lolaMessageId) => {
      const reply = get().messages.find((m) => m.id === lolaMessageId);
      const q = reply?.replyTo ? get().messages.find((m) => m.id === reply.replyTo) : undefined;
      if (!q) return;
      await get().send(q.text, { channel: q.channel, attachments: q.attachments });
    },

    approve: async (messageId, actionId) => {
      const setStatus = (status: ActionPreview['status']) =>
        patchMsg(messageId, (m) => ({ ...m, actions: m.actions.map((a) => (a.id === actionId ? { ...a, status } : a)) }));
      const msg = get().messages.find((m) => m.id === messageId);
      const action = msg?.actions.find((a) => a.id === actionId);
      if (!action || !action.can_approve || action.status !== 'proposed') return;
      setStatus('executing');
      try {
        const receipt = await getTransport().approve(actionId);
        patchMsg(messageId, (m) => ({ ...m, receipts: [...m.receipts.filter((r) => r.action_id !== actionId), receipt] }));
        if (receipt.status === 'failed') setStatus('failed');
        else if (receipt.status === 'executed' && receipt.verified) setStatus('executed');
        else setStatus('submitted'); // accepted, awaiting verification — never "done"
        receiptListeners.forEach((fn) => { try { fn(receipt, msg!.language); } catch { /* ignore speaker errors */ } });
      } catch (e) {
        setStatus('failed');
        patchMsg(messageId, (m) => ({
          ...m,
          receipts: [...m.receipts.filter((r) => r.action_id !== actionId), {
            action_id: actionId, status: 'failed', verified: false,
            message: (e as Error).message || 'The action could not be completed. Nothing changed.',
          }],
        }));
      }
    },

    reject: async (messageId, actionId) => {
      patchMsg(messageId, (m) => ({ ...m, actions: m.actions.map((a) => (a.id === actionId ? { ...a, status: 'rejected' } : a)) }));
      try { await getTransport().reject(actionId); } catch { /* rejection is local-safe: nothing executes */ }
    },

    newConversation: () => {
      get().interrupt();
      set({ messages: [], conversationId: null });
    },

    setLanguage: (language) => set({ language }),
    loadConversation: (conversationId, rows) => {
      get().interrupt();
      const lang = get().language;
      set({
        conversationId,
        messages: rows.map((r) => ({
          id: nextId(r.role === 'user' ? 'u' : 'l'),
          role: r.role === 'user' ? 'user' : 'lola',
          text: r.content,
          language: lang,
          channel: 'text',
          status: 'complete',
          cards: [], actions: [], receipts: [], notices: [], suggestions: [],
          startedAt: r.created_at ? new Date(r.created_at).getTime() : Date.now(),
        })),
      });
    },
    setVoiceState: (voiceState) => set({ voiceState }),
  };
});

/** Derive the orb's state from everything Lola is doing. Pure → testable. */
export function deriveOrbState(s: Pick<LolaState, 'messages' | 'streamingId' | 'voiceState' | 'workingStep'>, online = true): OrbState {
  if (!online) return 'error';
  if (s.voiceState === 'listening') return 'listening';
  if (s.streamingId) return s.workingStep ? 'working' : 'thinking';
  if (s.voiceState === 'speaking') return 'speaking';
  const anyExecuting = s.messages.some((m) => m.actions.some((a) => a.status === 'executing'));
  if (anyExecuting) return 'working';
  const last = [...s.messages].reverse().find((m) => m.role === 'lola');
  if (last?.status === 'error') return 'error';
  const awaiting = s.messages.some((m) => m.actions.some((a) => a.status === 'proposed' && a.can_approve));
  if (awaiting) return 'awaiting_approval';
  if (last && (last.notices.some((n) => n.kind === 'connector_outage') || last.actions.some((a) => a.status === 'failed'))) return 'attention';
  return 'idle';
}
