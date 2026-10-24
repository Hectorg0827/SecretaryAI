import { useEffect, useRef, useState } from 'react';
import { Menu } from 'lucide-react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
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
import { PrivateRoute, RoleGuard } from './components/auth/RouteGuards';

/** Shell with sidebar + main area — only shown for authenticated routes. */
function AppShell() {
  const [unreadCount, setUnreadCount] = useState(0);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const openedRef = useRef(false);

  // Keyboard focus must stay inside the mobile modal navigation. Closing it
  // restores focus to the opener, rather than stranding it on an inert link.
  useEffect(() => {
    if (!mobileMenuOpen) {
      if (openedRef.current) menuButtonRef.current?.focus();
      openedRef.current = false;
      return;
    }
    openedRef.current = true;
    const sidebar = document.getElementById('secretaryai-sidebar');
    const closeButton = sidebar?.querySelector<HTMLButtonElement>('[data-mobile-close]');
    closeButton?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setMobileMenuOpen(false);
        return;
      }
      if (event.key !== 'Tab' || !sidebar) return;
      const focusable = Array.from(
        sidebar.querySelectorAll<HTMLElement>('button:not([disabled]), a[href]'),
      ).filter((element) => element.getClientRects().length > 0);
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [mobileMenuOpen]);

  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 768px)');
    const dismissOnDesktop = () => {
      if (desktop.matches) setMobileMenuOpen(false);
    };
    desktop.addEventListener('change', dismissOnDesktop);
    return () => desktop.removeEventListener('change', dismissOnDesktop);
  }, []);

  return (
    <div className="flex h-dvh flex-col bg-slate-50 overflow-hidden md:flex-row">
      {/* Navigation stays a real landmark on mobile; drawer is closed by default. */}
      <header className="flex h-14 flex-shrink-0 items-center gap-3 border-b border-slate-200 bg-white px-4 md:hidden">
        <button
          type="button"
          aria-label="Open navigation"
          aria-expanded={mobileMenuOpen}
          aria-controls="secretaryai-sidebar"
          ref={menuButtonRef}
          onClick={() => setMobileMenuOpen(true)}
          className="rounded-lg p-2 text-slate-800 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-blue-600"
        >
          <Menu size={20} aria-hidden="true" />
        </button>
        <span className="font-semibold text-slate-900">SecretaryAI</span>
      </header>
      <Sidebar unreadCount={unreadCount} mobileOpen={mobileMenuOpen} onMobileClose={() => setMobileMenuOpen(false)} />
      <main className="min-h-0 min-w-0 flex-1 overflow-hidden">
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
        toastOptions={{ style: { fontSize: '13px', borderRadius: '10px' } }}
      />
    </BrowserRouter>
  );
}
