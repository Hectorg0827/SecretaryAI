import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('../auth', () => ({ authFetch: vi.fn() }));
import { authFetch } from '../auth';
import { lolaTurn, type DesktopLolaEvent } from './client';

const sse = (lines: string[], status = 200) =>
  new Response(new ReadableStream({ start(c) { lines.forEach((l) => c.enqueue(new TextEncoder().encode(l))); c.close(); } }), { status });

async function all(q = 'hi') {
  const out: DesktopLolaEvent[] = [];
  for await (const e of lolaTurn(q, undefined, 'en', new AbortController().signal)) out.push(e);
  return out;
}

describe('desktop Lola client', () => {
  afterEach(() => vi.mocked(authFetch).mockReset());

  it('falls back to the legacy stream and maps it to the shared contract', async () => {
    vi.mocked(authFetch)
      .mockResolvedValueOnce(new Response('{}', { status: 404 }))
      .mockResolvedValueOnce(sse(['data: {"text":"Hello","conversation_id":"c1"}\n', 'data: {"done":true}\n']));
    expect(await all()).toEqual([{ type: 'conversation', conversation_id: 'c1' }, { type: 'text', delta: 'Hello' }, { type: 'done' }]);
  });

  it('a stream without "done" ends as an error, never a success', async () => {
    vi.mocked(authFetch).mockResolvedValueOnce(sse(['data: {"text":"Partial"}\n']));
    const ev = await all();
    expect(ev[ev.length - 1]).toMatchObject({ type: 'error', retryable: true });
  });

  it('403 becomes a permission notice', async () => {
    vi.mocked(authFetch).mockResolvedValueOnce(new Response('{}', { status: 403 }));
    expect((await all())[0]).toMatchObject({ type: 'notice', notice: { kind: 'permission_denied' } });
  });
});
