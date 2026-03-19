import React from 'react';
import clsx from 'clsx';
import { Message } from '../../stores/chatStore';

interface Props {
  message: Message;
}

export function MessageBubble({ message }: Props) {
  const isUser = message.role === 'user';

  return (
    <div className={clsx('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={clsx(
          'max-w-[80%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed',
          isUser
            ? 'bg-blue-600 text-white rounded-br-sm'
            : 'bg-gray-100 text-gray-800 rounded-bl-sm',
        )}
      >
        {message.content || (message.streaming ? <span className="opacity-60">...</span> : null)}
        {message.streaming && message.content && (
          <span className="inline-block w-1 h-3.5 bg-current ml-0.5 animate-pulse rounded-sm" />
        )}
      </div>
    </div>
  );
}
