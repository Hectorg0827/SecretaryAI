import React from 'react';
import { ChatInterface } from '../components/chat/ChatInterface';

export function Chat() {
  return (
    <div className="h-screen p-6">
      <div className="max-w-3xl mx-auto h-full">
        <ChatInterface />
      </div>
    </div>
  );
}
