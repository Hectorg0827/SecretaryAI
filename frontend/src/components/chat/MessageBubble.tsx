import { Sparkles } from 'lucide-react';
import type { Message } from '../../stores/chatStore';

interface Props { message: Message }

export function MessageBubble({ message }: Props) {
  const isUser = message.role === 'user';
  return (
    <div className={`lola-chat__message ${isUser ? 'lola-chat__message--user' : ''}`}>
      {!isUser && <span className="lola-chat__avatar" aria-hidden="true"><Sparkles size={15} /></span>}
      <div className="lola-chat__bubble">
        {message.content || (message.streaming ? <span className="opacity-60">Thinking…</span> : null)}
        {message.streaming && message.content && <span className="inline-block w-1 h-3.5 bg-current ml-0.5 animate-pulse rounded-sm" />}
      </div>
    </div>
  );
}
