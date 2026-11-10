/**
 * Transport abstraction: the UI talks to Lola only through this interface.
 *
 *   HttpLolaTransport — real backend (Workstream 1). Default.
 *   MockLolaTransport — scripted demo/test data. ONLY when explicitly enabled
 *                       (VITE_LOLA_MOCK=1 or ?lola_demo=1 outside production),
 *                       and every result it produces is badged "Demo data".
 */
import type { Attachment, Briefing, LolaEvent, Receipt, TurnRequest } from './contract';
import type { Role } from '../hooks/useAuth';

export interface LolaTransport {
  readonly kind: 'http' | 'mock';
  /** Stream a turn. Abort via `signal` to interrupt Lola mid-answer. */
  turn(req: TurnRequest, signal: AbortSignal): AsyncGenerator<LolaEvent>;
  /** Approve a previewed action; resolves only with the server's receipt. */
  approve(actionId: string): Promise<Receipt>;
  reject(actionId: string, reason?: string): Promise<void>;
  /** Optional: re-check a submitted action's receipt against the execution ledger (null = not supported). */
  getReceipt?(actionId: string): Promise<Receipt | null>;
  /** Optional: download the CSV of an APPROVED supplier payment run (Lola pays nothing). */
  downloadPaymentRun?(receiptId: string): Promise<void>;
  briefing(role: Role, signal?: AbortSignal): Promise<Briefing>;
  /**
   * Optional: turn a PDF / .xlsx into text on the server (nothing is stored),
   * so it can be sent inline like a CSV. Resolves null when the server has no
   * extraction endpoint; rejects with a user-facing message when the file is refused.
   */
  extractFile?(file: File): Promise<Attachment | null>;
}

/**
 * Parse an SSE byte stream into JSON payloads. Tolerates chunk boundaries
 * anywhere, CRLF line endings, `[DONE]` sentinels and non-JSON lines.
 */
export async function* parseSse(body: ReadableStream<Uint8Array>, signal?: AbortSignal): AsyncGenerator<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const onAbort = () => { reader.cancel().catch(() => undefined); };
  signal?.addEventListener('abort', onAbort);
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        const raw = line.slice(5).trim();
        if (!raw || raw === '[DONE]') continue;
        try { yield JSON.parse(raw); } catch { /* skip non-JSON */ }
      }
    }
    const tail = buffer.trim();
    if (tail.startsWith('data:')) {
      try { yield JSON.parse(tail.slice(5).trim()); } catch { /* ignore */ }
    }
  } finally {
    signal?.removeEventListener('abort', onAbort);
  }
}

export function mockEnabled(): boolean {
  const env = (import.meta as unknown as { env?: Record<string, string | boolean | undefined> }).env ?? {};
  if (env.VITE_LOLA_MOCK === '1' || env.VITE_LOLA_MOCK === 'true') return true;
  if (env.PROD) return false; // never silently fall into demo data in production
  try {
    return new URLSearchParams(window.location.search).get('lola_demo') === '1'
      || window.localStorage.getItem('lola_demo') === '1';
  } catch {
    return false;
  }
}
