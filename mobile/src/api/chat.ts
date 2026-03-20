import { API_BASE, getToken, parseSseStream } from './client';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  created_at?: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface StreamChunk {
  text?: string;
  action?: Record<string, unknown>;
  conversation_id?: string;
  done?: boolean;
}

export async function* streamChat(
  message: string,
  conversationId?: string,
): AsyncGenerator<StreamChunk> {
  const token = await getToken();
  const res = await fetch(`${API_BASE}/api/chat/message`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      message,
      ...(conversationId ? { conversation_id: conversationId } : {}),
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? `Chat error: ${res.status}`);
  }

  for await (const event of parseSseStream(res)) {
    yield event as StreamChunk;
  }
}

export async function getChatHistory(
  conversationId: string,
  limit = 40,
): Promise<{ messages: ChatMessage[] }> {
  const token = await getToken();
  const res = await fetch(
    `${API_BASE}/api/chat/history/${conversationId}?limit=${limit}`,
    {
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    },
  );
  if (!res.ok) throw new Error(`Failed to load chat history: ${res.status}`);
  return res.json();
}

export async function getConversations(
  limit = 20,
): Promise<{ conversations: Conversation[] }> {
  const token = await getToken();
  const res = await fetch(`${API_BASE}/api/chat/conversations?limit=${limit}`, {
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  if (!res.ok) throw new Error(`Failed to load conversations: ${res.status}`);
  return res.json();
}

/**
 * Upload a recorded audio file URI to the backend Whisper transcription endpoint.
 * Returns the transcribed text.
 */
export async function transcribeAudio(audioUri: string): Promise<string> {
  const token = await getToken();
  const formData = new FormData();
  formData.append('audio', {
    uri: audioUri,
    type: 'audio/m4a',
    name: 'recording.m4a',
  } as unknown as Blob);

  const res = await fetch(`${API_BASE}/api/chat/transcribe`, {
    method: 'POST',
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      // Do NOT set Content-Type — let fetch set the multipart boundary automatically
    },
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? `Transcription error: ${res.status}`);
  }

  const data = await res.json();
  return data.text ?? '';
}
