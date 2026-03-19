import React, { useState } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';
import { Sidebar } from './components/layout/Sidebar';
import { Dashboard } from './pages/Dashboard';
import { Chat } from './pages/Chat';
import { Accounts } from './pages/Accounts';
import { Inventory } from './pages/Inventory';
import { Settings } from './pages/Settings';

export function App() {
  const [unreadCount, setUnreadCount] = useState(0);

  return (
    <BrowserRouter>
      <div className="flex h-screen bg-slate-50 overflow-hidden">
        <Sidebar unreadCount={unreadCount} />
        <main className="flex-1 overflow-hidden">
          <Routes>
            <Route path="/"          element={<Dashboard onUnreadChange={setUnreadCount} />} />
            <Route path="/chat"      element={<Chat />} />
            <Route path="/accounts"  element={<Accounts />} />
            <Route path="/inventory" element={<Inventory />} />
            <Route path="/settings"  element={<Settings />} />
          </Routes>
        </main>
      </div>
      <Toaster
        position="bottom-right"
        toastOptions={{
          style: { fontSize: '13px', borderRadius: '10px' },
        }}
      />
    </BrowserRouter>
  );
}
