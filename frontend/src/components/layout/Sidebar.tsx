import React, { useEffect, useRef, useState } from 'react';
import { NavLink } from 'react-router-dom';
import clsx from 'clsx';
import { Wifi, WifiOff, LogOut, UserCircle } from 'lucide-react';
import { api, AgentStatus } from '../../lib/api';
import { useAuth, Role } from '../../hooks/useAuth';
import { useInboxStore } from '../../stores/inboxStore';
import { LionBrand } from '../lola/LionBrand';
import { LionUIIcon } from '../lola/LionUIIcon';
import './lion-sidebar.css';
import { BRAND_LABELS } from '../../lib/brand';

const ROLE_LABELS: Record<Role, string> = {
  owner:       'Owner',
  manager:     'Manager',
  sales_rep:   'Sales Rep',
  back_office: 'Back Office',
  viewer:      'Viewer',
  finance_controller: 'Finance Controller',
  ar_specialist: 'Accounts Receivable',
  ap_specialist: 'Accounts Payable',
  pricing_billbacks: 'Pricing & Billbacks',
};

const ALL_NAV_ITEMS = [
  { to: '/',          label: BRAND_LABELS.home, icon: 'home', exact: true, roles: ['owner', 'manager', 'sales_rep', 'back_office', 'viewer', 'finance_controller', 'ar_specialist', 'ap_specialist', 'pricing_billbacks'], badge: null },
  { to: '/inbox',     label: 'Inbox',     icon: 'inbox',          exact: true,  roles: ['owner', 'manager', 'sales_rep', 'back_office'], badge: 'inbox'     },
  { to: '/work',      label: 'Work',      icon: 'work',      exact: true,  roles: ['owner', 'manager', 'sales_rep', 'back_office'], badge: null        },
  { to: '/dashboard', label: 'Detailed Dashboard', icon: 'dashboard', exact: true, roles: ['owner', 'manager', 'sales_rep', 'back_office', 'viewer'], badge: null },
  { to: '/finance',   label: 'Finance Workspace', icon: 'finance', exact: true, roles: ['owner', 'manager', 'finance_controller', 'ar_specialist', 'ap_specialist', 'pricing_billbacks'], badge: null },
  // Rep account assignment is not yet server-verified; fail closed until the
  // domain owner provides a scoped account read contract.
  { to: '/accounts',  label: 'Accounts',  icon: 'accounts',         exact: false, roles: ['owner', 'manager', 'back_office'], badge: null        },
  { to: '/inventory', label: 'Inventory', icon: 'inventory',       exact: false, roles: ['owner', 'manager', 'sales_rep', 'back_office'], badge: null        },
  { to: '/insights',  label: 'Insights',  icon: 'insights',     exact: false, roles: ['owner', 'manager', 'sales_rep', 'back_office'], badge: null        },
  { to: '/logistics',   label: 'Logistics',   icon: 'logistics',     exact: false, roles: ['owner', 'manager', 'back_office'],             badge: null        },
  { to: '/compliance',  label: 'Compliance',  icon: 'compliance',     exact: false, roles: ['owner', 'manager'],                            badge: null        },
  { to: '/connectors', label: 'Connectors', icon: 'connectors',     exact: false, roles: ['owner', 'manager'],                            badge: null        },
  { to: '/audit-log',  label: 'Audit Log',  icon: 'audit',    exact: false, roles: ['owner', 'manager'],                            badge: null        },
  { to: '/admin',      label: 'Diagnostics', icon: 'diagnostics',  exact: false, roles: ['owner', 'manager'],                            badge: null        },
  { to: '/settings',  label: 'Settings',  icon: 'settings',      exact: false, roles: ['owner', 'manager'],                            badge: null        },
  { to: '/account-security', label: 'My Password', icon: 'security', exact: true, roles: ['owner', 'manager', 'sales_rep', 'back_office', 'viewer', 'finance_controller', 'ar_specialist', 'ap_specialist', 'pricing_billbacks'], badge: null },
] as const;

export function Sidebar({
  mobileOpen = false,
  onMobileClose = () => {},
}: {
  mobileOpen?: boolean;
  onMobileClose?: () => void;
}) {
  const { role } = useAuth();
  const [agent, setAgent] = useState<AgentStatus | null>(null);
  const [userName, setUserName] = useState<string | null>(null);
  const inboxUnread = useInboxStore((s) => s.unread_count);
  const inboxFilter = useInboxStore((s) => s.filter);
  // A visually offscreen drawer must also be removed from keyboard and
  // assistive-technology navigation. Desktop links remain fully accessible.
  const sidebarRef = useRef<HTMLElement>(null);
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== 'undefined' && !!window.matchMedia?.('(max-width: 767px)').matches,
  );
  useEffect(() => {
    const query = window.matchMedia('(max-width: 767px)');
    const update = () => setIsMobile(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    if (sidebarRef.current) sidebarRef.current.inert = isMobile && !mobileOpen;
  }, [isMobile, mobileOpen]);


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
  const agentConfigured = Boolean(agent?.last_seen);
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
      ref={sidebarRef}
      id="secretaryai-sidebar"
      aria-label="Primary navigation"
      role={isMobile && mobileOpen ? 'dialog' : undefined}
      aria-modal={isMobile && mobileOpen ? true : undefined}
      className={[
        'lionbi-sidebar fixed inset-y-0 left-0 z-50 flex h-dvh w-60 flex-shrink-0 flex-col bg-[#0b1122] text-white border-r border-[#2d344b]',
        'transition-transform duration-200 md:static md:h-full md:translate-x-0 md:transition-none',
        mobileOpen ? 'translate-x-0' : '-translate-x-full',
      ].join(' ')}
    >
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-4 py-5 border-b border-[#30354a]">
        <button
          type="button"
          data-mobile-close="true"
          aria-label="Close navigation"
          className="order-2 ml-auto rounded-md p-1 text-slate-200 focus-visible:outline-2 focus-visible:outline-white md:hidden"
          onClick={onMobileClose}
        >
          <span aria-hidden="true">×</span>
        </button>
        <div className="order-1"><LionBrand /></div>
      </div>

      {/* Navigation */}
      <nav className="min-h-0 flex-1 overflow-y-auto px-3 py-4 space-y-0.5">
        {visibleItems.map(({ to, label, icon: Icon, exact, badge }) => {
          // The dashboard's email-only metric is not the unified Inbox count.
          // The Inbox API count is filtered and paginated; only expose it as a
          // badge when the unfiltered feed has actually been read successfully.
          const badgeCount = badge === 'inbox' && inboxFilter === 'all' ? inboxUnread : null;
          return (
            <NavLink
              key={to}
              to={to}
              end={exact}
              onClick={onMobileClose}
              className={({ isActive }) =>
                clsx(
                  'lionbi-navlink flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors relative',
                  isActive
                    ? 'lionbi-navlink--active bg-[#252a47] text-[#f3dca8]'
                    : 'text-[#a9b5cd] hover:text-white hover:bg-[#1c2743]',
                )
              }
            >
              <LionUIIcon kind={Icon} size={19} />
              {label}
              {badgeCount !== null && badgeCount > 0 && (
                <span
                  aria-label={`${badgeCount} unread items in the last loaded Inbox page`}
                  title="Unread count in the most recently loaded unfiltered Inbox page (up to 50 items)"
                  className="ml-auto bg-red-500 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full min-w-[18px] text-center leading-none"
                >
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
            <div className="text-[10px] text-slate-300 capitalize">{ROLE_LABELS[role]}</div>
          </div>
        </div>

        {/* Agent status */}
        <div className={clsx(
          'flex items-center gap-2 text-xs px-3 py-2 rounded-lg',
          connected ? 'bg-emerald-900/40 text-emerald-400' : 'bg-slate-800 text-slate-300',
        )}>
          {connected ? <Wifi size={12} /> : <WifiOff size={12} />}
          <span>{connected ? 'Agent online' : agentConfigured ? 'Agent offline' : 'Agent not configured'}</span>
          {connected && (
            <span className="ml-auto w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
          )}
        </div>

        <button
          onClick={logout}
          className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-xs text-slate-300 hover:text-red-400 hover:bg-slate-800/60 transition-colors"
        >
          <LogOut size={12} />
          Sign out
        </button>
        <div className="text-[10px] text-slate-300 px-1">v1.0.0</div>
      </div>
    </aside>
    </>
  );
}
