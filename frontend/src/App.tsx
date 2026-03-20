import React, { useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';
import { Sidebar } from './components/layout/Sidebar';
import { Dashboard } from './pages/Dashboard';
import { Chat } from './pages/Chat';
import { Accounts } from './pages/Accounts';
import { Inventory } from './pages/Inventory';
import { Settings } from './pages/Settings';
import { Login } from './pages/Login';

/** Guard: redirects to /login if no JWT is stored. */
function PrivateRoute({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const token = localStorage.getItem('secretary_token');
  if (!token) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return <>{children}</>;
}

/** Shell with sidebar + main area — only shown for authenticated routes. */
function AppShell() {
  const [unreadCount, setUnreadCount] = useState(0);

  return (
    <div className="flex h-screen bg-slate-50 overflow-hidden">
      <Sidebar unreadCount={unreadCount} />
      <main className="flex-1 overflow-hidden">
        <Routes>
          <Route path="/"          element={<Dashboard onUnreadChange={setUnreadCount} />} />
          <Route path="/chat"      element={<Chat />} />
          <Route path="/accounts"  element={<Accounts />} />
          <Route path="/inventory" element={<Inventory />} />
          <Route path="/settings"  element={<Settings />} />
          {/* Catch-all → dashboard */}
          <Route path="*"          element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Public */}
        <Route path="/login" element={<Login />} />

        {/* Private — everything else goes through PrivateRoute */}
        <Route
          path="/*"
          element={
            <PrivateRoute>
              <AppShell />
            </PrivateRoute>
          }
        />
      </Routes>

      <Toaster
        position="bottom-right"
        toastOptions={{ style: { fontSize: '13px', borderRadius: '10px' } }}
      />
    </BrowserRouter>
  );
}
