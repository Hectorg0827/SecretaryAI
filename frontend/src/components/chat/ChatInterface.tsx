import React, { useEffect, useRef, useState } from 'react';
import { LionUIIcon } from '../lola/LionUIIcon';
import { useChatStore } from '../../stores/chatStore';
import { contextKey, type LolaContext } from '../../lib/lolaContext';
import { MessageBubble } from './MessageBubble';
import { LolaOrb } from '../lola/LolaOrb';
import { LolaGlyph } from '../lola/LolaGlyph';
import { useLolaVoiceStore } from '../../stores/lolaVoiceStore';
import './lola-chat.css';

const QUICK_PROMPTS = [
  'What needs my attention?',
  'Review my business priorities',
  'Which accounts need attention?',
  'Show inventory risks',
];

export function ChatInterface({
  onBack, onClose, compact = false, context, initialHistoryOpen = false,
}: {
  onBack?: () => void;
  onClose?: () => void;
  compact?: boolean;
  context?: LolaContext;
  initialHistoryOpen?: boolean;
} = {}) {
  const { messages, isStreaming, conversationId, context: activeContext, sendMessage, clearConversation,
    beginContext, conversations, historyLoading, loadingMore, hasMore, historyError, deletingId, deleteConversation,
    loadConversations, loadMoreConversations, openConversation } = useChatStore();
  const [historyOpen, setHistoryOpen] = useState(initialHistoryOpen);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const messagesRef = useRef<HTMLDivElement>(null);
  const followLatestRef = useRef(true);
  const speak = useLolaVoiceStore((state) => state.speak);
  const stopSpeaking = useLolaVoiceStore((state) => state.stop);
  const voiceStatus = useLolaVoiceStore((state) => state.status);
  const lastAnswer = [...messages].reverse().find((message) =>
    message.role === 'assistant' && !message.streaming && message.content.trim().length > 0);
  const initialContextKey = contextKey(context ?? null);
  useEffect(() => {
    if (context) beginContext(context);
    // The context object may be regenerated; only switch when its identity changes.
  }, [initialContextKey, beginContext]);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  const canReadAloud = typeof window !== 'undefined' &&
    'speechSynthesis' in window && typeof SpeechSynthesisUtterance !== 'undefined';

  // Only follow the newest reply if the reader is near the bottom.
  // Never yank the page away while someone is reviewing older evidence.
  useEffect(() => {
    const area = messagesRef.current;
    if (area && followLatestRef.current) area.scrollTop = area.scrollHeight;
  }, [messages, isStreaming]);

  const trackScroll = () => {
    const area = messagesRef.current;
    if (!area) return;
    followLatestRef.current = area.scrollHeight - area.scrollTop - area.clientHeight < 110;
  };

  const handleSend = (value = input) => {
    const text = value.trim();
    if (!text || isStreaming) return;
    setInput('');
    followLatestRef.current = true;
    void sendMessage(text);
  };

  const handleKey = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      handleSend();
    }
  };

  return (
    <section className={compact ? "lola-chat lola-chat--compact" : "lola-chat"} aria-label="Lola conversation">
      <div className="lola-chat__topline">
        <div className="lola-chat__heading">
          {onBack && <button type="button" className="lola-chat__back" onClick={onBack} aria-label="Overview"><LionUIIcon kind="back" size={16} /><span>Overview</span></button>}
          <div className="lola-chat__identity"><LolaOrb state={isStreaming ? "thinking" : undefined} className="lola-orb--mini" /><div><strong>Lola</strong><small>Your business intelligence assistant</small></div></div>
        </div>
        <div className="lola-chat__controls">
          <button type="button" className="lola-chat__history-toggle" aria-label="Conversation history" aria-controls="lola-chat-history"
            aria-expanded={historyOpen} onClick={() => { setHistoryOpen(!historyOpen); if (!historyOpen) void loadConversations(); }}>
            <LionUIIcon kind="history" size={16} /> <span>History</span>
          </button>
          {onClose && <button type="button" className="lola-chat__clear" aria-label="Close Lola assistant"
            onClick={onClose}><LionUIIcon kind="close" size={17} /></button>}
          <button type="button" className="lola-chat__clear" onClick={() => { clearConversation(); setHistoryOpen(false); }}
            disabled={isStreaming} aria-label="Start a new conversation" title="New conversation"><LionUIIcon kind="refresh" size={17} /></button>
        </div>
      </div>
      {historyOpen && (
        <div id="lola-chat-history" className="lola-chat__history" aria-label="Your saved conversations">
          <div className="lola-chat__history-heading">
            <strong>Conversation history</strong>
            <button type="button" onClick={() => void loadConversations()} disabled={historyLoading}>Refresh</button>
          </div>
          {historyError && <p className="lola-chat__history-state" role="alert">{historyError}</p>}
          {historyLoading ? <p className="lola-chat__history-state" role="status">Loading conversations…</p>
            : conversations.length === 0 ? <p className="lola-chat__history-state">No saved conversations yet.</p>
            : <div className="lola-chat__history-list">
                {conversations.map((conversation) => (
                  <div className={`lola-chat__history-item ${conversation.conversation_id === conversationId ? 'is-active' : ''}`}
                    key={conversation.conversation_id}>
                    <button type="button" className="lola-chat__history-open"
                      aria-label={`Open conversation: ${conversation.preview || 'Untitled'}`}
                      onClick={async () => { if (await openConversation(conversation.conversation_id)) { setHistoryOpen(false); setConfirmDelete(null); } }}
                      disabled={isStreaming || !!deletingId || historyLoading}>
                      <span>{conversation.preview || 'Untitled conversation'}</span>
                      <small>{conversation.created_at ? new Date(conversation.created_at).toLocaleDateString() : 'Saved conversation'}</small>
                    </button>
                    <button type="button" className="lola-chat__history-delete"
                      title="Delete conversation"
                      aria-label={`Delete conversation: ${conversation.preview || 'Untitled'}`}
                      disabled={isStreaming || !!deletingId}
                      onClick={() => setConfirmDelete(conversation.conversation_id)}><LionUIIcon kind="delete" size={15} /></button>
                    {confirmDelete === conversation.conversation_id && (
                      <div className="lola-chat__confirm" role="group" aria-label="Confirm conversation deletion">
                        <span>Delete permanently?</span>
                        <button type="button" onClick={() => setConfirmDelete(null)}>Cancel</button>
                        <button type="button" className="is-danger" disabled={!!deletingId}
                          onClick={async () => { if (await deleteConversation(conversation.conversation_id)) setConfirmDelete(null); }}>
                          {deletingId === conversation.conversation_id ? 'Deleting…' : 'Delete'}
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>}
          {hasMore && !historyLoading && (
            <button type="button" className="lola-chat__more" disabled={loadingMore}
              onClick={() => void loadMoreConversations()}>
              {loadingMore ? 'Loading older conversations…' : 'Load older conversations'}
            </button>
          )}
        </div>
      )}
      {activeContext && activeContext.workspace !== 'home' && (
        <div className="lola-chat__context" role="status">
          <span>Lola is viewing <strong>{activeContext.workspace}</strong>
            {activeContext.entity_label ? ` · ${activeContext.entity_label}` : ''}</span>
          <small>Page context is a navigation hint. Business details still require authorized sources.</small>
        </div>
      )}
      <div ref={messagesRef} onScroll={trackScroll} className="lola-chat__messages" role="log" aria-label="Conversation messages" aria-live="polite">
        {messages.length === 0 && (
          <div className="lola-chat__welcome">
            <LolaGlyph size={42} className="lola-chat__welcome-mark" />
            <div className="lola-chat__eyebrow">LOLA · YOUR BUSINESS ASSISTANT</div>
            <h2>How can I help you move forward?</h2>
            <p>Ask a question, explore your business, or prepare your next decision.</p>
            <div className="lola-chat__prompts">
              {QUICK_PROMPTS.map((question) => (
                <button type="button" key={question} onClick={() => handleSend(question)} disabled={isStreaming}>{question}<LionUIIcon kind="up" size={14} /></button>
              ))}
            </div>
          </div>
        )}
        {messages.map((msg) => <MessageBubble key={msg.id} message={msg} />)}
        {isStreaming && messages[messages.length - 1]?.role === 'user' && (
          <div className="lola-chat__thinking" role="status"><LionUIIcon kind="loading" size={15} className="animate-spin" /> Lola is thinking…</div>
        )}
      </div>
      <div className="lola-chat__bottom">
        <div className="lola-chat__composer">
          <label className="sr-only" htmlFor="lola-chat-input">Message Lola</label>
          <textarea id="lola-chat-input" value={input} onChange={e => setInput(e.target.value)} onKeyDown={handleKey}
            placeholder="Ask Lola about your business…" rows={2} maxLength={4000} disabled={isStreaming} />
          <button type="button" onClick={() => handleSend()} disabled={!input.trim() || isStreaming} aria-label="Send message to Lola">
            {isStreaming ? <LionUIIcon kind="loading" size={18} className="animate-spin" /> : <LionUIIcon kind="up" size={19} />}
          </button>
        </div>
        {canReadAloud && lastAnswer && !isStreaming && (
          <button type="button" className="lola-chat__read" onClick={() => {
            if (voiceStatus === 'speaking') stopSpeaking();
            else speak(lastAnswer.content);
          }} aria-label={voiceStatus === 'speaking' ? 'Stop Lola voice' : 'Read Lola response aloud'}>
            {voiceStatus === 'speaking' ? <LionUIIcon kind="voice-off" size={15} /> : <LionUIIcon kind="voice" size={15} />}
            {voiceStatus === 'speaking' ? 'Stop voice' : 'Read response aloud'}
          </button>
        )}
        <p className="lola-chat__privacy"><LionUIIcon kind="compliance" size={12} /> Responses depend on your permissions and connected business data.</p>
      </div>
    </section>
  );
}
