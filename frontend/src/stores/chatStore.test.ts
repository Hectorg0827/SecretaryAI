import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn(), streamChat: vi.fn(), delete: vi.fn() }));
vi.mock('../lib/api', () => ({ api: mocks }));

import { useChatStore } from './chatStore';

beforeEach(() => {
  vi.clearAllMocks();
  useChatStore.setState({
    messages: [], isStreaming: false, conversationId: null,
    conversations: [], historyLoading: false, loadingMore: false, hasMore: false,
    historyError: null, deletingId: null, context: null,
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
    expect(mocks.get).toHaveBeenCalledWith('/api/chat/conversations?limit=50&offset=0');
  });

  it('loads older conversations without duplicating saved entries', async () => {
    mocks.get.mockResolvedValueOnce({
      conversations: [{conversation_id:'one',preview:'First'}], has_more:true,
    }).mockResolvedValueOnce({
      conversations: [{conversation_id:'two',preview:'Second'}], has_more:false,
    });
    await useChatStore.getState().loadConversations();
    expect(useChatStore.getState().hasMore).toBe(true);
    await useChatStore.getState().loadMoreConversations();
    expect(mocks.get).toHaveBeenLastCalledWith('/api/chat/conversations?limit=50&offset=1');
    expect(useChatStore.getState().conversations.map((c) => c.conversation_id)).toEqual(['one','two']);
    expect(useChatStore.getState().hasMore).toBe(false);
  });

  it('restores a saved conversation without trusting malformed messages', async () => {
    mocks.get.mockResolvedValue({
      conversation_id: 'conv-1',
      messages: [
        { role: 'user', content: 'Question', context: {workspace: 'accounts',entity_type:'account',entity_label:'Synthetic Account'} },
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

  it('autosaves a real response by passing page context through the shared streaming client', async () => {
    const stream = async function* () {
      yield { text: 'Verified answer', conversationId: 'saved-1' };
    };
    mocks.streamChat.mockImplementation(stream);
    mocks.get.mockResolvedValue({ conversations: [
      { conversation_id: 'saved-1', preview: 'Question' },
    ] });
    useChatStore.getState().beginContext({workspace:'accounts',entity_type:'account',entity_label:'Synthetic Account'});
    await useChatStore.getState().sendMessage('Summarize this account');
    expect(mocks.streamChat).toHaveBeenCalledWith(
      'Summarize this account',
      undefined,
      {workspace:'accounts',entity_type:'account',entity_label:'Synthetic Account'},
    );
    expect(useChatStore.getState().conversationId).toBe('saved-1');
    expect(useChatStore.getState().conversations).toHaveLength(1);
  });

  it('deletes only the selected conversation and clears it when currently open', async () => {
    useChatStore.setState({
      conversationId: 'mine',
      conversations: [{conversation_id:'mine',preview:'My conversation'},
        {conversation_id:'other',preview:'Another conversation'}],
      messages: [{id:'1',role:'user',content:'Hello',timestamp:new Date()}],
    });
    mocks.delete.mockResolvedValue({ deleted: true, conversation_id: 'mine' });
    expect(await useChatStore.getState().deleteConversation('mine')).toBe(true);
    expect(mocks.delete).toHaveBeenCalledWith('/api/chat/conversations/mine');
    expect(useChatStore.getState().messages).toEqual([]);
    expect(useChatStore.getState().conversationId).toBeNull();
    expect(useChatStore.getState().conversations.map((c) => c.conversation_id)).toEqual(['other']);
  });

  it('preserves a conversation when deletion fails', async () => {
    useChatStore.setState({ conversationId: 'mine', conversations: [{conversation_id:'mine',preview:'Private'}] });
    mocks.delete.mockRejectedValue(new Error('403 Forbidden'));
    expect(await useChatStore.getState().deleteConversation('mine')).toBe(false);
    expect(useChatStore.getState().conversationId).toBe('mine');
    expect(useChatStore.getState().historyError).toMatch(/could not be deleted/i);
  });

  it('starts a fresh conversation when a different account context opens', () => {
    useChatStore.setState({
      context: {workspace:'accounts',entity_type:'account',entity_label:'Customer A'},
      conversationId: 'customer-a',
      messages: [{id:'old',role:'user',content:'Customer A data',timestamp:new Date()}],
    });
    useChatStore.getState().beginContext({workspace:'accounts',entity_type:'account',entity_label:'Customer B'});
    expect(useChatStore.getState().conversationId).toBeNull();
    expect(useChatStore.getState().messages).toHaveLength(0);
    expect(useChatStore.getState().context?.entity_label).toBe('Customer B');
  });

  it('cannot clear a conversation mid-stream', () => {
    useChatStore.setState({ isStreaming: true, conversationId: 'active' });
    useChatStore.getState().clearConversation();
    expect(useChatStore.getState().conversationId).toBe('active');
  });
});
