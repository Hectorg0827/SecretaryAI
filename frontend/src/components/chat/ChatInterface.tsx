import React, { useEffect, useRef, useState } from 'react';
import { Send, RotateCcw, Loader2 } from 'lucide-react';
import { useChatStore } from '../../stores/chatStore';
import { MessageBubble } from './MessageBubble';

const QUICK_PROMPTS = [
  'How\'s our inventory looking?',
  '¿Cómo están nuestras cuentas?',
  'Which accounts need attention?',
  'What\'s running low this week?',
];

export function ChatInterface() {
  const { messages, isStreaming, sendMessage, clearConversation } = useChatStore();
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = () => {
    const text = input.trim();
    if (!text || isStreaming) return;
    setInput('');
    sendMessage(text);
  };

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex flex-col h-full bg-white rounded-xl shadow-sm border border-gray-200">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-green-400 rounded-full" />
          <span className="font-semibold text-gray-800">SecretaryAI</span>
        </div>
        <button
          onClick={clearConversation}
          className="text-gray-400 hover:text-gray-600 transition-colors"
          title="Clear conversation"
        >
          <RotateCcw size={16} />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="space-y-4">
            <p className="text-center text-gray-400 text-sm mt-8">
              Ask me anything about your business
            </p>
            <div className="grid grid-cols-2 gap-2 mt-4">
              {QUICK_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  onClick={() => sendMessage(prompt)}
                  className="text-left px-3 py-2 text-sm text-gray-600 bg-gray-50 hover:bg-gray-100 rounded-lg border border-gray-200 transition-colors"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}

        {isStreaming && messages[messages.length - 1]?.role === 'user' && (
          <div className="flex items-center gap-2 text-gray-400 text-sm">
            <Loader2 size={14} className="animate-spin" />
            <span>Thinking...</span>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-4 py-3 border-t border-gray-100">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Ask about accounts, inventory, orders..."
            rows={1}
            disabled={isStreaming}
            className="flex-1 resize-none rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
            style={{ maxHeight: '120px', overflowY: 'auto' }}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || isStreaming}
            className="flex-shrink-0 bg-blue-600 hover:bg-blue-700 text-white rounded-lg p-2 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {isStreaming ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <Send size={16} />
            )}
          </button>
        </div>
        <p className="text-xs text-gray-400 mt-1">Enter to send · Shift+Enter for new line</p>
      </div>
    </div>
  );
}
