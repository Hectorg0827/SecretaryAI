import React, { useState } from 'react';
import clsx from 'clsx';
import { MessageSquare, X, Minimize2 } from 'lucide-react';
import { ChatInterface } from './ChatInterface';

/**
 * Floating chat button + slide-in drawer panel.
 * Shows the full ChatInterface in a right-side panel
 * without navigating away from the dashboard.
 */
export function ChatDrawer() {
  const [open,     setOpen]     = useState(false);
  const [minimized, setMinimized] = useState(false);

  return (
    <>
      {/* Floating toggle button (bottom-right, above toaster) */}
      {!open && (
        <button
          onClick={() => { setOpen(true); setMinimized(false); }}
          className="fixed bottom-6 right-6 z-40 flex items-center gap-2 px-4 py-3 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold rounded-full shadow-lg transition-all hover:shadow-xl active:scale-95"
        >
          <MessageSquare size={16} />
          Live Chat
        </button>
      )}

      {/* Slide-in panel */}
      <div className={clsx(
        'fixed right-0 top-0 h-screen z-50 flex flex-col transition-transform duration-300 ease-in-out',
        open ? 'translate-x-0' : 'translate-x-full',
        minimized ? 'w-72' : 'w-[420px]',
      )}>
        {/* Panel header */}
        <div className="flex items-center gap-3 bg-slate-900 px-4 py-3 flex-shrink-0">
          <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-white text-sm font-semibold flex-1">Live Conversations</span>
          <button
            onClick={() => setMinimized(!minimized)}
            className="text-slate-400 hover:text-white transition-colors p-1 rounded"
            title={minimized ? 'Expand' : 'Minimize'}
          >
            <Minimize2 size={14} />
          </button>
          <button
            onClick={() => setOpen(false)}
            className="text-slate-400 hover:text-white transition-colors p-1 rounded"
            title="Close"
          >
            <X size={14} />
          </button>
        </div>

        {/* Chat body */}
        {!minimized && (
          <div className="flex-1 overflow-hidden shadow-2xl">
            <ChatInterface />
          </div>
        )}

        {/* Minimized footer — click to restore */}
        {minimized && (
          <button
            onClick={() => setMinimized(false)}
            className="flex-1 bg-white border-l border-slate-200 text-xs text-slate-500 flex items-center justify-center hover:bg-slate-50 transition-colors"
          >
            Click to expand
          </button>
        )}
      </div>

      {/* Backdrop */}
      {open && (
        <div
          className="fixed inset-0 bg-black/20 z-40 backdrop-blur-sm"
          onClick={() => setOpen(false)}
        />
      )}
    </>
  );
}
