import React, { useEffect, useState } from 'react';
import { Wifi, WifiOff, Monitor, Apple, RefreshCw } from 'lucide-react';
import { api, AgentStatus } from '../../lib/api';

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  return 'Good evening';
}

function formatDate(): string {
  return new Date().toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric',
  });
}

function PlatformIcon({ platform }: { platform: string | null }) {
  if (platform === 'windows') return <Monitor size={11} className="opacity-70" />;
  if (platform === 'macos')   return <Apple    size={11} className="opacity-70" />;
  return null;
}

export function TopBar() {
  const [agent, setAgent]     = useState<AgentStatus | null>(null);
  const [rotating, setRotating] = useState(false);

  const fetchStatus = async () => {
    try {
      const s = await api.agent.status();
      setAgent(s);
    } catch { /* no agent configured yet */ }
  };

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 60_000);
    return () => clearInterval(interval);
  }, []);

  const refresh = () => {
    setRotating(true);
    fetchStatus().finally(() => setTimeout(() => setRotating(false), 600));
  };

  return (
    <div className="h-14 flex-shrink-0 border-b border-slate-200 bg-white px-6 flex items-center justify-between">
      {/* Greeting */}
      <div>
        <span className="text-sm font-semibold text-slate-800">{greeting()}</span>
        <span className="ml-2 text-sm text-slate-400">{formatDate()}</span>
      </div>

      {/* Agent status pill */}
      <div className="flex items-center gap-3">
        <button
          onClick={refresh}
          className="text-slate-400 hover:text-slate-600 transition-colors"
          title="Refresh status"
        >
          <RefreshCw size={13} className={rotating ? 'animate-spin' : ''} />
        </button>

        {agent === null ? (
          <span className="text-xs text-slate-400">Agent not configured</span>
        ) : agent.connected ? (
          <div className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-700 text-xs font-medium">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            <PlatformIcon platform={agent.platform} />
            <span>Agent connected</span>
            {agent.agent_version && (
              <span className="opacity-60 ml-0.5">v{agent.agent_version}</span>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-slate-100 border border-slate-200 text-slate-500 text-xs font-medium">
            <WifiOff size={11} />
            <span>Agent offline</span>
          </div>
        )}
      </div>
    </div>
  );
}
