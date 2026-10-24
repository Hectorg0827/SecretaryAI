import React from 'react';
import { ChatInterface } from '../components/chat/ChatInterface';

export function Chat() {
  return (
    <div className="h-full overflow-y-auto p-4 sm:p-6">
      <div className="max-w-3xl mx-auto h-full">
        <ChatInterface />
      </div>
    </div>
  );
}
