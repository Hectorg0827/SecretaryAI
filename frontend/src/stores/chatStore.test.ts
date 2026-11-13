import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn(), streamChat: vi.fn() }));
vi.mock('../lib/api', () => ({ api: mocks }));

import { useChatStore } from './chatStore';

beforeEach(() => {
  vi.clearAllMocks();
  useChatStore.setState({
    messages: [], isStreaming: false, conversationId: null,
    conversations: [], historyLoading: false, historyError: null,
  });
});

describe('Lola conversation history', () => {
  it('loads only validated server conversation summaries', async () => {
    mocks.get.mockResolvedValue({
      conversations: [
        { conversation_id: 'alice-1', preview: 'Inventory risks' },
        { conversation_id: 'invalid', preview: 123 },
      ],
    });
    await useChatStore.getState().loadConversations();
    expect(useChatStore.getState().conversations).toEqual([
      { conversation_id: 'alice-1', preview: 'Inventory risks' },
    ]);
    expect(mocks.get).toHaveBeenCalledWith('/api/chat/conversations?limit=100');
  });

  it('restores a saved conversation without trusting malformed messages', async () => {
    mocks.get.mockResolvedValue({
      conversation_id: 'conv-1',
      messages: [
        { role: 'user', content: 'Question' },
        { role: 'assistant', content: 'Answer' },
        { role: 'system', content: 'This should never appear' },
      ],
    });
    expect(await useChatStore.getState().openConversation('conv-1')).toBe(true);
    expect(useChatStore.getState().conversationId).toBe('conv-1');
    expect(useChatStore.getState().messages.map((message) => message.content))
      .toEqual(['Question', 'Answer']);
  });

  it('does not replace the open conversation on an inaccessible history request', async () => {
    useChatStore.setState({ conversationId: 'mine', messages: [
      { id: 'existing', role: 'user', content: 'Keep this', timestamp: new Date() },
    ] });
    mocks.get.mockRejectedValue(new Error('Not found'));
    expect(await useChatStore.getState().openConversation('other-user')).toBe(false);
    expect(useChatStore.getState().conversationId).toBe('mine');
    expect(useChatStore.getState().messages[0].content).toBe('Keep this');
    expect(useChatStore.getState().historyError).toMatch(/could not be opened/i);
  });

  it('fails closed on malformed history rather than treating it as empty', async () => {
    mocks.get.mockResolvedValue({ conversation_id: 'conv-1', messages: 'not an array' });
    expect(await useChatStore.getState().openConversation('conv-1')).toBe(false);
    expect(useChatStore.getState().conversationId).toBeNull();
  });

  it('cannot clear a conversation mid-stream', () => {
    useChatStore.setState({ isStreaming: true, conversationId: 'active' });
    useChatStore.getState().clearConversation();
    expect(useChatStore.getState().conversationId).toBe('active');
  });
});
