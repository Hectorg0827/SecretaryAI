import React, { useEffect, useRef, useState } from 'react';
import { ArrowUp, History, Loader2, RotateCcw, ShieldCheck, Volume2, VolumeX } from 'lucide-react';
import { useChatStore } from '../../stores/chatStore';
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

export function ChatInterface() {
  const { messages, isStreaming, conversationId, sendMessage, clearConversation, conversations, historyLoading, historyError, loadConversations, openConversation } = useChatStore();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const speak = useLolaVoiceStore((state) => state.speak);
  const stopSpeaking = useLolaVoiceStore((state) => state.stop);
  const voiceStatus = useLolaVoiceStore((state) => state.status);
  const lastAnswer = [...messages].reverse().find((message) =>
    message.role === 'assistant' && !message.streaming && message.content.trim().length > 0);
  const canReadAloud = typeof window !== 'undefined' &&
    'speechSynthesis' in window && typeof SpeechSynthesisUtterance !== 'undefined';

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  const handleSend = (value = input) => {
    const text = value.trim();
    if (!text || isStreaming) return;
    setInput('');
    void sendMessage(text);
  };

  const handleKey = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      handleSend();
    }
  };

  return (
    <section className="lola-chat" aria-label="Lola conversation">
      <div className="lola-chat__topline">
        <div className="lola-chat__identity"><LolaOrb state={isStreaming ? "thinking" : undefined} className="lola-orb--mini" /><div><strong>Lola</strong><small>Your business intelligence assistant</small></div></div>
        <div className="lola-chat__controls">
          <button type="button" className="lola-chat__history-toggle" aria-controls="lola-chat-history"
            aria-expanded={historyOpen} onClick={() => { setHistoryOpen(!historyOpen); if (!historyOpen) void loadConversations(); }}>
            <History size={16} /> <span>Recent</span>
          </button>
          <button type="button" className="lola-chat__clear" onClick={() => { clearConversation(); setHistoryOpen(false); }}
            disabled={isStreaming} aria-label="Start a new conversation" title="New conversation"><RotateCcw size={17} /></button>
        </div>
      </div>
      {historyOpen && (
        <div id="lola-chat-history" className="lola-chat__history" aria-label="Your saved conversations">
          <div className="lola-chat__history-heading">
            <strong>Recent conversations</strong>
            <button type="button" onClick={() => void loadConversations()} disabled={historyLoading}>Refresh</button>
          </div>
          {historyLoading ? <p className="lola-chat__history-state" role="status">Loading saved conversations…</p>
            : historyError ? <p className="lola-chat__history-state" role="alert">{historyError}</p>
            : conversations.length === 0 ? <p className="lola-chat__history-state">No saved conversations yet.</p>
            : <div className="lola-chat__history-list">
                {conversations.map((conversation) => (
                  <button type="button" key={conversation.conversation_id}
                    className={conversation.conversation_id === conversationId ? 'is-active' : ''}
                    onClick={async () => { if (await openConversation(conversation.conversation_id)) setHistoryOpen(false); }}
                    disabled={isStreaming || historyLoading}
                    title={conversation.preview || 'Untitled conversation'}>
                    {conversation.preview || 'Untitled conversation'}
                  </button>
                ))}
              </div>}
        </div>
      )}
      <div className="lola-chat__messages" role="log" aria-label="Conversation messages" aria-live="polite">
        {messages.length === 0 && (
          <div className="lola-chat__welcome">
            <LolaGlyph size={42} className="lola-chat__welcome-mark" />
            <div className="lola-chat__eyebrow">LOLA · YOUR BUSINESS ASSISTANT</div>
            <h2>How can I help you move forward?</h2>
            <p>Ask a question, explore your business, or prepare your next decision.</p>
            <div className="lola-chat__prompts">
              {QUICK_PROMPTS.map((question) => (
                <button type="button" key={question} onClick={() => handleSend(question)} disabled={isStreaming}>{question}<ArrowUp size={14} /></button>
              ))}
            </div>
          </div>
        )}
        {messages.map((msg) => <MessageBubble key={msg.id} message={msg} />)}
        {isStreaming && messages[messages.length - 1]?.role === 'user' && (
          <div className="lola-chat__thinking" role="status"><Loader2 size={15} className="animate-spin" /> Lola is thinking…</div>
        )}
        <div ref={bottomRef} />
      </div>
      <div className="lola-chat__bottom">
        <div className="lola-chat__composer">
          <label className="sr-only" htmlFor="lola-chat-input">Message Lola</label>
          <textarea id="lola-chat-input" value={input} onChange={e => setInput(e.target.value)} onKeyDown={handleKey}
            placeholder="Ask Lola about your business…" rows={2} maxLength={4000} disabled={isStreaming} />
          <button type="button" onClick={() => handleSend()} disabled={!input.trim() || isStreaming} aria-label="Send message to Lola">
            {isStreaming ? <Loader2 size={18} className="animate-spin" /> : <ArrowUp size={19} />}
          </button>
        </div>
        {canReadAloud && lastAnswer && !isStreaming && (
          <button type="button" className="lola-chat__read" onClick={() => {
            if (voiceStatus === 'speaking') stopSpeaking();
            else speak(lastAnswer.content);
          }} aria-label={voiceStatus === 'speaking' ? 'Stop Lola voice' : 'Read Lola response aloud'}>
            {voiceStatus === 'speaking' ? <VolumeX size={15} /> : <Volume2 size={15} />}
            {voiceStatus === 'speaking' ? 'Stop voice' : 'Read response aloud'}
          </button>
        )}
        <p className="lola-chat__privacy"><ShieldCheck size={12} /> Responses depend on your permissions and connected business data.</p>
      </div>
    </section>
  );
}
