import { useState, useRef, useEffect } from 'react';
import { Bot, X, Send, ChevronDown, ChevronUp, Loader2 } from 'lucide-react';
import { api } from '../../lib/api';

interface Message {
  role: 'user' | 'assistant';
  content: string;
  streaming?: boolean;
}

interface AssistantContext {
  type: 'account' | 'inventory' | 'inbox' | 'work' | 'general';
  label?: string;
  data?: Record<string, unknown>;
}

interface Props {
  context?: AssistantContext;
  initialSuggestion?: string;
  onClose?: () => void;
  className?: string;
}

const CONTEXT_PROMPTS: Record<string, string[]> = {
  account:   ['Draft a follow-up email', 'Summarise recent orders', 'Flag as at-risk'],
  inventory: ['Draft a reorder PO', 'Check lead times', 'Forecast stock-out date'],
  inbox:     ['Summarise this item', 'Draft a reply', 'What action do you recommend?'],
  work:      ['What should I tackle first?', 'Any overdue items?'],
  general:   ['How are we doing this week?', 'Any urgent issues?', 'Inventory status?'],
};

export function InlineAssistant({ context, initialSuggestion, onClose, className = '' }: Props) {
  const [messages, setMessages]     = useState<Message[]>([]);
  const [input, setInput]           = useState('');
  const [isStreaming, setStreaming]  = useState(false);
  const [collapsed, setCollapsed]   = useState(false);
  const [convId, setConvId]         = useState<string | null>(null);
  const bottomRef                   = useRef<HTMLDivElement>(null);

  const contextType = context?.type ?? 'general';
  const prompts     = CONTEXT_PROMPTS[contextType] ?? CONTEXT_PROMPTS.general;

  // Inject an initial AI suggestion if provided
  useEffect(() => {
    if (initialSuggestion) {
      setMessages([{ role: 'assistant', content: initialSuggestion }]);
    }
  }, [initialSuggestion]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const send = async (text: string) => {
    if (!text.trim() || isStreaming) return;
    const userMsg = text.trim();
    setInput('');

    // Prefix with context data if present
    const contextPrefix = context?.data
      ? `[Context: ${context.type} — ${context.label ?? ''}]\n${JSON.stringify(context.data, null, 2)}\n\n`
      : '';
    const fullMessage = contextPrefix + userMsg;

    setMessages((prev) => [
      ...prev,
      { role: 'user', content: userMsg },
      { role: 'assistant', content: '', streaming: true },
    ]);
    setStreaming(true);

    try {
      let full = '';
      for await (const chunk of api.streamChat(fullMessage, convId ?? undefined)) {
        if (chunk.conversation_id) setConvId(chunk.conversation_id);
        full += chunk.text ?? '';
        setMessages((prev) => {
          const updated = [...prev];
          const last    = updated[updated.length - 1];
          if (last?.role === 'assistant') updated[updated.length - 1] = { ...last, content: full };
          return updated;
        });
      }
    } catch {
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { role: 'assistant', content: 'Sorry, something went wrong.' };
        return updated;
      });
    } finally {
      setMessages((prev) => prev.map((m, i) => i === prev.length - 1 ? { ...m, streaming: false } : m));
      setStreaming(false);
    }
  };

  return (
    <div className={`flex flex-col bg-white border border-slate-200 rounded-xl shadow-lg overflow-hidden ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-slate-900 text-white">
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4 text-blue-400" />
          <span className="text-sm font-medium">
            AI Assistant
            {context?.label && <span className="text-slate-400 ml-1">— {context.label}</span>}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setCollapsed((c) => !c)}
            className="p-1 rounded hover:bg-slate-700 transition-colors"
          >
            {collapsed ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronUp className="w-3.5 h-3.5" />}
          </button>
          {onClose && (
            <button onClick={onClose} className="p-1 rounded hover:bg-slate-700 transition-colors">
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {!collapsed && (
        <>
          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0 max-h-72">
            {messages.length === 0 ? (
              <div className="text-center py-4">
                <Bot className="w-8 h-8 text-slate-300 mx-auto mb-2" />
                <p className="text-xs text-slate-400">Ask me anything about this {contextType}</p>
              </div>
            ) : (
              messages.map((msg, i) => (
                <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div
                    className={`max-w-[85%] rounded-lg px-3 py-2 text-xs leading-relaxed ${
                      msg.role === 'user'
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-100 text-slate-800'
                    }`}
                  >
                    {msg.content}
                    {msg.streaming && (
                      <span className="inline-block w-1 h-3 bg-slate-400 rounded ml-0.5 animate-pulse" />
                    )}
                  </div>
                </div>
              ))
            )}
            <div ref={bottomRef} />
          </div>

          {/* Quick prompts */}
          {messages.length === 0 && (
            <div className="px-3 pb-2 flex flex-wrap gap-1.5">
              {prompts.map((p) => (
                <button
                  key={p}
                  onClick={() => send(p)}
                  disabled={isStreaming}
                  className="text-xs px-2.5 py-1 rounded-full bg-slate-100 text-slate-600 hover:bg-blue-50 hover:text-blue-700 border border-slate-200 transition-colors disabled:opacity-50"
                >
                  {p}
                </button>
              ))}
            </div>
          )}

          {/* Input */}
          <div className="px-3 pb-3 pt-1 border-t border-slate-100">
            <div className="flex items-center gap-2 bg-slate-50 rounded-lg border border-slate-200 px-3 py-2">
              <input
                className="flex-1 bg-transparent text-xs outline-none placeholder-slate-400"
                placeholder="Ask anything..."
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && send(input)}
                disabled={isStreaming}
              />
              <button
                onClick={() => send(input)}
                disabled={isStreaming || !input.trim()}
                className="text-blue-600 disabled:opacity-30 hover:text-blue-800 transition-colors"
              >
                {isStreaming ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Send className="w-3.5 h-3.5" />
                )}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
