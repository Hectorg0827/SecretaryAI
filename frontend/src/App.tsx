import React, { useEffect, useState } from 'react';
import { Menu } from 'lucide-react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';
import { Sidebar } from './components/layout/Sidebar';
import { Dashboard } from './pages/Dashboard';
import { Chat } from './pages/Chat';
import { Accounts } from './pages/Accounts';
import { Inventory } from './pages/Inventory';
import { Settings } from './pages/Settings';
import { Login } from './pages/Login';
import { Register } from './pages/Register';
import Inbox from './pages/Inbox';
import Work from './pages/Work';
import { Logistics } from './pages/Logistics';
import { Compliance } from './pages/Compliance';
import { Connectors } from './pages/Connectors';
import { AuditLog } from './pages/AuditLog';
import { AdminDiag } from './pages/AdminDiag';
import { useAuth, Role } from './hooks/useAuth';
import { LolaDock } from './lola/components/LolaDock';
import { CommandCenter } from './lola/components/CommandCenter';

/** Guard: redirects to /login if no JWT is stored. */
function PrivateRoute({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const token = localStorage.getItem('secretary_token');
  if (!token) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return <>{children}</>;
}

/**
 * Role guard: redirects to / if the user's role is not in the allowed list.
 * Must be used inside PrivateRoute (token already confirmed).
 */
function RoleGuard({ roles, children }: { roles: Role[]; children: React.ReactNode }) {
  const { role } = useAuth();
  if (!roles.includes(role)) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}

/** Shell with sidebar + main area — only shown for authenticated routes. */
function AppShell() {
  const [unreadCount, setUnreadCount] = useState(0);
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  useEffect(() => { setNavOpen(false); }, [location.pathname]);

  return (
    <div className="flex h-[100dvh] bg-slate-50 overflow-hidden">
      {/* Desktop sidebar */}
      <div className="hidden md:flex">
        <Sidebar unreadCount={unreadCount} />
      </div>
      {/* Mobile navigation drawer */}
      {navOpen && (
        <div className="fixed inset-0 z-50 flex md:hidden" role="dialog" aria-label="Navigation" onKeyDown={(e) => e.key === 'Escape' && setNavOpen(false)}>
          <Sidebar unreadCount={unreadCount} />
          <button type="button" aria-label="Close navigation" className="flex-1 bg-black/40" onClick={() => setNavOpen(false)} />
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-3 border-b border-slate-800 bg-slate-900 px-3 py-2 text-white md:hidden">
          <button type="button" aria-label="Open navigation" aria-expanded={navOpen} onClick={() => setNavOpen(true)} className="rounded-lg p-2 hover:bg-slate-800">
            <Menu size={18} />
          </button>
          <span className="font-bold tracking-tight">Lion BI</span>
        </div>
      <main className="min-h-0 flex-1 overflow-hidden">
        <Routes>
          <Route path="/" element={<Dashboard onUnreadChange={setUnreadCount} />} />
          <Route
            path="/inbox"
            element={
              <RoleGuard roles={['owner', 'manager', 'sales_rep', 'back_office']}>
                <Inbox />
              </RoleGuard>
            }
          />
          <Route
            path="/work"
            element={
              <RoleGuard roles={['owner', 'manager', 'sales_rep', 'back_office']}>
                <Work />
              </RoleGuard>
            }
          />
          <Route
            path="/lola"
            element={
              <RoleGuard roles={['owner', 'manager', 'sales_rep', 'back_office']}>
                <CommandCenter />
              </RoleGuard>
            }
          />
          <Route
            path="/chat"
            element={
              <RoleGuard roles={['owner', 'manager', 'sales_rep']}>
                <Chat />
              </RoleGuard>
            }
          />
          <Route
            path="/accounts"
            element={
              <RoleGuard roles={['owner', 'manager', 'sales_rep', 'back_office']}>
                <Accounts />
              </RoleGuard>
            }
          />
          <Route
            path="/inventory"
            element={
              <RoleGuard roles={['owner', 'manager', 'sales_rep', 'back_office']}>
                <Inventory />
              </RoleGuard>
            }
          />
          <Route
            path="/settings"
            element={
              <RoleGuard roles={['owner', 'manager']}>
                <Settings />
              </RoleGuard>
            }
          />
          <Route
            path="/logistics"
            element={
              <RoleGuard roles={['owner', 'manager']}>
                <Logistics />
              </RoleGuard>
            }
          />
          <Route
            path="/compliance"
            element={
              <RoleGuard roles={['owner', 'manager']}>
                <Compliance />
              </RoleGuard>
            }
          />
          <Route
            path="/connectors"
            element={
              <RoleGuard roles={['owner', 'manager']}>
                <Connectors />
              </RoleGuard>
            }
          />
          <Route
            path="/audit-log"
            element={
              <RoleGuard roles={['owner', 'manager']}>
                <AuditLog />
              </RoleGuard>
            }
          />
          <Route
            path="/admin"
            element={
              <RoleGuard roles={['owner', 'manager']}>
                <AdminDiag />
              </RoleGuard>
            }
          />
          {/* Catch-all → dashboard */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      </div>
      <LolaDock />
    </div>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Public */}
        <Route path="/login"    element={<Login />} />
        <Route path="/register" element={<Register />} />

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
        containerStyle={{ bottom: 96 }}
        toastOptions={{ style: { fontSize: '13px', borderRadius: '10px' } }}
      />
    </BrowserRouter>
  );
}
