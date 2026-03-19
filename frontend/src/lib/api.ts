/**
 * API client for the SecretaryAI backend.
 * All requests are authenticated via JWT Bearer token.
 */

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

function getToken(): string | null {
  return localStorage.getItem('secretary_token');
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const token = getToken();
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(error.detail ?? `HTTP ${response.status}`);
  }

  return response.json();
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body: unknown) => request<T>('POST', path, body),
  patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  delete: <T>(path: string) => request<T>('DELETE', path),

  /** Streaming chat — returns an async generator of text chunks */
  async *streamChat(message: string, conversationId?: string): AsyncGenerator<string> {
    const token = getToken();
    const response = await fetch(`${API_BASE}/api/chat/message`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ message, conversation_id: conversationId }),
    });

    if (!response.ok || !response.body) {
      throw new Error(`Chat request failed: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const text = decoder.decode(value, { stream: true });
      for (const line of text.split('\n')) {
        if (line.startsWith('data: ') && !line.includes('[DONE]')) {
          yield line.slice(6);
        }
      }
    }
  },
};
