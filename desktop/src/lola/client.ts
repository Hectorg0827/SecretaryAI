/**
 * Lola client for the desktop app. Speaks the same contract as the web app
 * (docs/lola/CONTRACT.md): POST /api/lola/turn (SSE of LolaEvent), falling
 * back to the legacy /api/chat/message stream. Business logic stays on the
 * server; this file only transports and validates.
 *
 * Honesty rule (shared with web): a turn is complete only when the server
 * sends `done`. Anything else ends as an error that says nothing ran.
 */
import { authFetch } from '../auth';

export type DesktopLolaEvent =
  | { type: 'conversation'; conversation_id: string }
  | { type: 'status'; state: 'thinking' | 'working'; step?: string }
  | { type: 'text'; delta: string }
  | { type: 'card'; card: { id: string; title: string; availability: string; availability_note?: string } }
  | { type: 'action_preview'; action: { id: string; title: string; summary: string } }
  | { type: 'notice'; notice: { kind: string; message: string } }
  | { type: 'error'; message: string; retryable: boolean }
  | { type: 'done' };

const KNOWN = new Set(['conversation', 'status', 'text', 'card', 'action_preview', 'notice', 'error', 'done', 'language', 'receipt', 'suggestions']);

export async function* sseJson(body: ReadableStream<Uint8Array>): AsyncGenerator<Record<string, unknown>> {
  const reader = body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split(/\r?\n/);
    buf = lines.pop() ?? '';
    for (const l of lines) {
      if (!l.startsWith('data:')) continue;
      const raw = l.slice(5).trim();
      if (!raw || raw === '[DONE]') continue;
      try { yield JSON.parse(raw); } catch { /* skip */ }
    }
  }
}

let lolaEndpoint: boolean | null = null;

export async function* lolaTurn(message: string, conversationId: string | undefined, language: 'en' | 'es', signal: AbortSignal): AsyncGenerator<DesktopLolaEvent> {
  try {
    let res: Response | null = null;
    let legacy = false;
    if (lolaEndpoint !== false) {
      res = await authFetch('/api/lola/turn', {
        method: 'POST', signal,
        body: JSON.stringify({ message, conversation_id: conversationId, language, channel: 'text', context_route: 'desktop' }),
      });
      if (res.status === 404 || res.status === 405) { lolaEndpoint = false; res = null; } else lolaEndpoint = true;
    }
    if (!res) {
      legacy = true;
      res = await authFetch('/api/chat/message', { method: 'POST', signal, body: JSON.stringify({ message, conversation_id: conversationId }) });
    }
    if (res.status === 403) {
      yield { type: 'notice', notice: { kind: 'permission_denied', message: 'Your role doesn’t have access to this.' } };
      yield { type: 'done' };
      return;
    }
    if (!res.ok || !res.body) {
      yield { type: 'error', message: `Lola couldn’t reach the server (HTTP ${res.status}). Nothing was executed.`, retryable: true };
      return;
    }
    let done = false;
    let seenConv = '';
    for await (const p of sseJson(res.body)) {
      if (!legacy && KNOWN.has(String(p.type))) {
        if (p.type === 'done') done = true;
        yield p as unknown as DesktopLolaEvent;
        continue;
      }
      if (typeof p.conversation_id === 'string' && p.conversation_id !== seenConv) {
        seenConv = p.conversation_id;
        yield { type: 'conversation', conversation_id: seenConv };
      }
      if (typeof p.text === 'string' && p.text) yield { type: 'text', delta: p.text };
      if (p.done === true) { done = true; yield { type: 'done' }; }
    }
    if (!done && !signal.aborted) {
      yield { type: 'error', message: 'The connection dropped before I finished. Nothing was executed.', retryable: true };
    }
  } catch {
    if (!signal.aborted) yield { type: 'error', message: 'I lost the connection to Lion BI. Nothing was executed.', retryable: true };
  }
}
