import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { parseSse } from '../transport';
import { HttpLolaTransport, mapLegacyPayload } from '../httpTransport';
import type { LolaEvent } from '../contract';
import { json, loginAs, sseResponse } from './helpers';

async function collect(gen: AsyncGenerator<LolaEvent>): Promise<LolaEvent[]> {
  const out: LolaEvent[] = [];
  for await (const e of gen) out.push(e);
  return out;
}

describe('parseSse', () => {
  it('handles split chunks, CRLF, [DONE] and junk lines', async () => {
    const res = sseResponse(['data: {"a":', '1}\r\n\r\nda', 'ta: [DONE]\n', ': comment\ndata: not-json\n', 'data: {"b":2}']);
    const out: unknown[] = [];
    for await (const p of parseSse(res.body!)) out.push(p);
    expect(out).toEqual([{ a: 1 }, { b: 2 }]);
  });
});

describe('legacy mapping', () => {
  it('maps text, conversation id once, actions → non-approvable notice, done', () => {
    const seen: { id?: string } = {};
    expect(mapLegacyPayload({ text: 'Hi', conversation_id: 'c1' }, seen)).toEqual([
      { type: 'conversation', conversation_id: 'c1' }, { type: 'text', delta: 'Hi' },
    ]);
    expect(mapLegacyPayload({ text: ' there', conversation_id: 'c1' }, seen)).toEqual([{ type: 'text', delta: ' there' }]);
    const action = mapLegacyPayload({ action: { action_type: 'draft_email' } }, seen);
    expect(action[0].type).toBe('notice');
    expect(action.some((e) => e.type === 'action_preview')).toBe(false);
    expect(mapLegacyPayload({ done: true }, seen)).toEqual([{ type: 'done' }]);
  });
});

describe('HttpLolaTransport', () => {
  beforeEach(() => loginAs('owner'));
  afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });
  const req = { message: 'hello', language: 'en' as const, channel: 'text' as const };

  it('uses /api/lola/turn when available', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(sseResponse([
      'data: {"type":"status","state":"working","step":"Checking QuickBooks"}\n',
      'data: {"type":"text","delta":"Done."}\n',
      'data: {"type":"done"}\n',
    ]));
    const ev = await collect(new HttpLolaTransport().turn(req, new AbortController().signal));
    expect(String(f.mock.calls[0][0])).toContain('/api/lola/turn');
    expect(ev.map((e) => e.type)).toEqual(['status', 'text', 'done']);
  });

  it('falls back to the legacy chat stream on 404', async () => {
    const f = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json(404, { detail: 'Not Found' }))
      .mockResolvedValueOnce(sseResponse(['data: {"text":"Hola","conversation_id":"c9"}\n', 'data: {"done":true,"conversation_id":"c9"}\n']));
    const ev = await collect(new HttpLolaTransport().turn(req, new AbortController().signal));
    expect(String(f.mock.calls[1][0])).toContain('/api/chat/message');
    expect(ev).toEqual([{ type: 'conversation', conversation_id: 'c9' }, { type: 'text', delta: 'Hola' }, { type: 'done' }]);
  });

  it('turns a stream that ends without "done" into an explicit error', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(sseResponse(['data: {"type":"text","delta":"Your invoices are"}\n']));
    const ev = await collect(new HttpLolaTransport().turn(req, new AbortController().signal));
    const last = ev[ev.length - 1];
    expect(last.type).toBe('error');
    expect(last.type === 'error' && last.message).toMatch(/nothing was executed/i);
  });

  it('maps 403 to a permission notice', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json(403, { detail: 'Insufficient permissions' }));
    const ev = await collect(new HttpLolaTransport().turn(req, new AbortController().signal));
    expect(ev[0]).toMatchObject({ type: 'notice', notice: { kind: 'permission_denied' } });
  });

  it('maps a network failure to a retryable error, never a result', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const ev = await collect(new HttpLolaTransport().turn(req, new AbortController().signal));
    expect(ev).toHaveLength(1);
    expect(ev[0]).toMatchObject({ type: 'error', retryable: true });
  });

  it('legacy approval returns an UNVERIFIED submitted receipt', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json(404, {}))
      .mockResolvedValueOnce(json(200, { status: 'approved' }));
    const r = await new HttpLolaTransport().approve('d1');
    expect(r).toMatchObject({ status: 'submitted', verified: false });
  });

  it('briefing fallback marks unreachable sources unavailable (never empty-success)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(503, { detail: 'down' }));
    const b = await new HttpLolaTransport().briefing('owner');
    const alerts = b.sections.find((s) => s.id === 'critical_alerts')!;
    expect(alerts.availability).toBe('unavailable');
    expect(alerts.items).toHaveLength(0);
    expect(b.sections.filter((s) => s.availability === 'ok')).toHaveLength(0);
  });
});

describe('HttpLolaTransport.extractFile (PDF / Excel)', () => {
  beforeEach(() => loginAs('owner'));
  afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });
  const file = new File(['%PDF-1.4'], 'po.pdf', { type: 'application/pdf' });

  it('returns the extracted text as an inline attachment', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      json(200, { name: 'po.pdf', mime: 'text/plain', size: 12, text: 'Olive Oil x 50' }));
    const a = await new HttpLolaTransport().extractFile(file);
    expect(a).toEqual({ name: 'po.pdf', size: file.size, mime: 'text/plain', text: 'Olive Oil x 50' });
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain('/api/lola/files');
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.headers as Record<string, string>).Authorization).toMatch(/^Bearer /);
  });

  it('resolves null on servers without the endpoint', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 404 }));
    expect(await new HttpLolaTransport().extractFile(file)).toBeNull();
  });

  it("surfaces the server's reason when a file is refused", async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(422, { detail: 'Password-protected PDFs can’t be read.' }));
    await expect(new HttpLolaTransport().extractFile(file)).rejects.toThrow('Password-protected');
  });
});
