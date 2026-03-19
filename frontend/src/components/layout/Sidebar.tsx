import React, { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import clsx from 'clsx';
import { LayoutDashboard, MessageSquare, Users, Package, Settings, Zap, Wifi, WifiOff } from 'lucide-react';
import { api, AgentStatus } from '../../lib/api';

const NAV_ITEMS = [
  { to: '/',          label: 'Dashboard', icon: LayoutDashboard, exact: true },
  { to: '/chat',      label: 'Chat',      icon: MessageSquare },
  { to: '/accounts',  label: 'Accounts',  icon: Users },
  { to: '/inventory', label: 'Inventory', icon: Package },
  { to: '/settings',  label: 'Settings',  icon: Settings },
];

export function Sidebar({ unreadCount = 0 }: { unreadCount?: number }) {
  const [agent, setAgent] = useState<AgentStatus | null>(null);

  useEffect(() => {
    const poll = () => api.agent.status().then(setAgent).catch(() => null);
    poll();
    const t = setInterval(poll, 60_000);
    return () => clearInterval(t);
  }, []);

  const connected = agent?.connected ?? false;

  return (
    <aside className="w-56 flex-shrink-0 bg-slate-900 text-white flex flex-col h-screen">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 py-5 border-b border-slate-800">
        <div className="w-7 h-7 bg-blue-500 rounded-lg flex items-center justify-center shadow-sm">
          <Zap size={14} className="text-white" />
        </div>
        <span className="font-bold text-white tracking-tight">SecretaryAI</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {NAV_ITEMS.map(({ to, label, icon: Icon, exact }) => (
          <NavLink
            key={to}
            to={to}
            end={exact}
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
            {label === 'Dashboard' && unreadCount > 0 && (
              <span className="ml-auto bg-blue-500 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full min-w-[18px] text-center leading-none">
                {unreadCount > 99 ? '99+' : unreadCount}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Footer — agent status */}
      <div className="px-4 py-4 border-t border-slate-800">
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
        <div className="text-[10px] text-slate-600 mt-2 px-1">v1.0.0</div>
      </div>
    </aside>
  );
}
