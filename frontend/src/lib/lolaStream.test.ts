import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';

function eventStream(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return {
    ok: true,
    status: 200,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
        controller.close();
      },
    }),
  } as unknown as Response;
}

async function collect(message: string, conversationId?: string) {
  const values: Array<{ text: string; conversationId?: string }> = [];
  for await (const value of api.streamChat(message, conversationId)) values.push(value);
  return values;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('Lola chat streaming contract', () => {
  it('reassembles JSON split across network frames and honors the done marker', async () => {
    localStorage.setItem('secretary_token', 'synthetic-session');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(eventStream([
      'data: {"text":"Hel',
      'lo","conversation_id":"conv-1"}\n\n',
      'data: {"text":" world","conversation_id":"conv-1"}\n\n',
      'data: {"done":true,"conversation_id":"conv-1"}\n\n',
    ])));
    const result = await collect('What needs attention?');
    expect(result.map((value) => value.text).join('')).toBe('Hello world');
    expect(result[0].conversationId).toBe('conv-1');
    const fetchArgs = vi.mocked(fetch).mock.calls[0];
    expect(fetchArgs[0]).toBe('/api/chat/message');
    expect((fetchArgs[1]?.headers as Record<string, string>).Authorization)
      .toBe('Bearer synthetic-session');
  });

  it('fails closed on a truncated stream rather than presenting a partial answer', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(eventStream([
      'data: {"text":"Unfinished","conversation_id":"conv-2"}\n\n',
    ])));
    await expect(collect('Question')).rejects.toThrow('incomplete');
  });

  it('fails on a successful empty stream rather than silently creating a blank message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(eventStream([
      'data: {"done":true,"conversation_id":"conv-3"}\n\n',
    ])));
    await expect(collect('Question')).rejects.toThrow('incomplete');
  });

  it('uses the one-time refresh flow for an expired authenticated chat request', async () => {
    localStorage.setItem('secretary_token', 'expired-session');
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 401 })
      .mockResolvedValueOnce({
        ok: true, status: 200,
        json: async () => ({ access_token: 'renewed-session' }),
      })
      .mockResolvedValueOnce(eventStream([
        'data: {"text":"Renewed","conversation_id":"conv-4"}\n\n',
        'data: {"done":true,"conversation_id":"conv-4"}\n\n',
      ]));
    vi.stubGlobal('fetch', fetchMock);
    const values = await collect('Resume', 'conv-4');
    expect(values[0].text).toBe('Renewed');
    expect(localStorage.getItem('secretary_token')).toBe('renewed-session');
    expect((fetchMock.mock.calls[2][1].headers as Record<string, string>).Authorization)
      .toBe('Bearer renewed-session');
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({
      message: 'Resume', conversation_id: 'conv-4',
    });
  });
});
