import { LolaGlyph } from '../lola/LolaGlyph';
import { RichMessage } from './RichMessage';
import type { Message } from '../../stores/chatStore';

interface Props { message: Message }

export function MessageBubble({ message }: Props) {
  const isUser = message.role === 'user';
  return (
    <article className={`lola-chat__message ${isUser ? 'lola-chat__message--user' : 'lola-chat__message--assistant'}`}
      aria-label={isUser ? 'You' : 'Lola'}>
      {!isUser && (
        <span className="lola-chat__avatar" aria-hidden="true"><LolaGlyph size={22} /></span>
      )}
      <div className="lola-chat__message-body">
        {!isUser && <span className="lola-chat__author">Lola</span>}
        <div className="lola-chat__bubble">
          {message.content ? (
            isUser ? <p className="lola-chat__user-text">{message.content}</p>
              : <RichMessage content={message.content} />
          ) : message.streaming ? (
            <span className="lola-chat__pending" role="status">Preparing your response…</span>
          ) : null}
          {message.streaming && message.content && (
            <span className="lola-chat__cursor" aria-hidden="true" />
          )}
        </div>
      </div>
    </article>
  );
}
