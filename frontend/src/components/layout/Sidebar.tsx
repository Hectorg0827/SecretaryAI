import React, { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import clsx from 'clsx';
import { LayoutDashboard, MessageSquare, Users, Package, Settings, Zap, Wifi, WifiOff, LogOut, UserCircle, Inbox, Briefcase, Truck, Scale, Server, Shield, Activity } from 'lucide-react';
import { api, AgentStatus } from '../../lib/api';
import { useAuth, Role } from '../../hooks/useAuth';
import { useInboxStore } from '../../stores/inboxStore';

const ROLE_LABELS: Record<Role, string> = {
  owner:       'Owner',
  manager:     'Manager',
  sales_rep:   'Sales Rep',
  back_office: 'Back Office',
  viewer:      'Viewer',
};

const ALL_NAV_ITEMS = [
  { to: '/inbox',     label: 'Inbox',     icon: Inbox,          exact: true,  roles: ['owner', 'manager', 'sales_rep', 'back_office'], badge: 'inbox'     },
  { to: '/work',      label: 'Work',      icon: Briefcase,      exact: true,  roles: ['owner', 'manager', 'sales_rep', 'back_office'], badge: null        },
  { to: '/',          label: 'Dashboard', icon: LayoutDashboard, exact: true, roles: ['owner', 'manager', 'sales_rep', 'back_office', 'viewer'], badge: null },
  { to: '/accounts',  label: 'Accounts',  icon: Users,         exact: false, roles: ['owner', 'manager', 'sales_rep', 'back_office'], badge: null        },
  { to: '/inventory', label: 'Inventory', icon: Package,       exact: false, roles: ['owner', 'manager', 'sales_rep', 'back_office'], badge: null        },
  { to: '/chat',      label: 'Chat',      icon: MessageSquare, exact: false, roles: ['owner', 'manager', 'sales_rep'],                badge: null        },
  { to: '/logistics',   label: 'Logistics',   icon: Truck,     exact: false, roles: ['owner', 'manager'],                            badge: null        },
  { to: '/compliance',  label: 'Compliance',  icon: Scale,     exact: false, roles: ['owner', 'manager'],                            badge: null        },
  { to: '/connectors', label: 'Connectors', icon: Server,     exact: false, roles: ['owner', 'manager'],                            badge: null        },
  { to: '/audit-log',  label: 'Audit Log',  icon: Shield,    exact: false, roles: ['owner', 'manager'],                            badge: null        },
  { to: '/admin',      label: 'Diagnostics', icon: Activity,  exact: false, roles: ['owner', 'manager'],                            badge: null        },
  { to: '/settings',  label: 'Settings',  icon: Settings,      exact: false, roles: ['owner', 'manager'],                            badge: null        },
] as const;

export function Sidebar({
  unreadCount = 0,
  mobileOpen = false,
  onMobileClose = () => {},
}: {
  unreadCount?: number;
  mobileOpen?: boolean;
  onMobileClose?: () => void;
}) {
  const { role } = useAuth();
  const [agent, setAgent] = useState<AgentStatus | null>(null);
  const [userName, setUserName] = useState<string | null>(null);
  const inboxUnread = useInboxStore((s) => s.unread_count);

  const logout = () => {
    onMobileClose();
    localStorage.removeItem('secretary_token');
    localStorage.removeItem('secretary_company_id');
    localStorage.removeItem('secretary_role');
    window.location.href = '/login';
  };

  useEffect(() => {
    const poll = () => api.agent.status().then(setAgent).catch(() => null);
    poll();
    const t = setInterval(poll, 60_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    api.get<{ name: string }>('/auth/me')
      .then((u) => setUserName(u.name))
      .catch(() => null);
  }, []);

  const connected = agent?.connected ?? false;
  const visibleItems = ALL_NAV_ITEMS.filter((item) => (item.roles as readonly string[]).includes(role));

  return (
    <>
      {mobileOpen && (
        <button
          type="button"
          aria-label="Close navigation overlay"
          className="fixed inset-0 z-40 bg-slate-950/60 md:hidden"
          onClick={onMobileClose}
        />
      )}
    <aside
      id="secretaryai-sidebar"
      aria-label="Primary navigation"
      className={[
        'fixed inset-y-0 left-0 z-50 flex h-dvh w-56 flex-shrink-0 flex-col bg-slate-900 text-white',
        'transition-transform duration-200 md:static md:h-full md:translate-x-0 md:transition-none',
        mobileOpen ? 'translate-x-0' : '-translate-x-full',
      ].join(' ')}
    >
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 py-5 border-b border-slate-800">
        <div className="w-7 h-7 bg-blue-500 rounded-lg flex items-center justify-center shadow-sm">
          <Zap size={14} className="text-white" />
        </div>
        <span className="font-bold text-white tracking-tight">SecretaryAI</span>
      </div>

      {/* Navigation */}
      <nav className="min-h-0 flex-1 overflow-y-auto px-3 py-4 space-y-0.5">
        {visibleItems.map(({ to, label, icon: Icon, exact, badge }) => {
          const badgeCount = badge === 'inbox' ? (inboxUnread || unreadCount) : 0;
          return (
            <NavLink
              key={to}
              to={to}
              end={exact}
              onClick={onMobileClose}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors relative',
                  isActive
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-400 hover:text-white hover:bg-slate-800',
                )
              }
            >
              <Icon size={16} />
              {label}
              {badgeCount > 0 && (
                <span className="ml-auto bg-red-500 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full min-w-[18px] text-center leading-none">
                  {badgeCount > 99 ? '99+' : badgeCount}
                </span>
              )}
            </NavLink>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-4 py-4 border-t border-slate-800 space-y-2">
        {/* User badge */}
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-800/60">
          <UserCircle size={14} className="text-slate-400 flex-shrink-0" />
          <div className="min-w-0">
            {userName && (
              <div className="text-xs font-medium text-slate-200 truncate">{userName}</div>
            )}
            <div className="text-[10px] text-slate-500 capitalize">{ROLE_LABELS[role]}</div>
          </div>
        </div>

        {/* Agent status */}
        <div className={clsx(
          'flex items-center gap-2 text-xs px-3 py-2 rounded-lg',
          connected ? 'bg-emerald-900/40 text-emerald-400' : 'bg-slate-800 text-slate-500',
        )}>
          {connected ? <Wifi size={12} /> : <WifiOff size={12} />}
          <span>{connected ? 'Agent online' : 'Agent offline'}</span>
          {connected && (
            <span className="ml-auto w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
          )}
        </div>

        <button
          onClick={logout}
          className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-xs text-slate-500 hover:text-red-400 hover:bg-slate-800/60 transition-colors"
        >
          <LogOut size={12} />
          Sign out
        </button>
        <div className="text-[10px] text-slate-600 px-1">v1.0.0</div>
      </div>
    </aside>
    </>
  );
}
