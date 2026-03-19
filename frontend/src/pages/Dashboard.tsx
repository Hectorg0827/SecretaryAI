import React, { useEffect, useState } from 'react';
import { AccountHealthCard } from '../components/dashboard/AccountHealthCard';
import { InventoryAlerts } from '../components/inventory/InventoryAlerts';
import { ChatInterface } from '../components/chat/ChatInterface';
import { api } from '../lib/api';

interface DashboardData {
  accounts: { healthy: number; slowing: number; at_risk: number; dormant: number };
  inventory_alerts: any[];
}

export function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get<DashboardData>('/api/dashboard/summary')
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Left panel: metrics */}
      <div className="flex-1 overflow-y-auto p-6 space-y-5">
        <h1 className="text-xl font-bold text-gray-900">Good morning 👋</h1>

        {loading ? (
          <div className="text-gray-400 text-sm">Loading...</div>
        ) : data ? (
          <>
            <AccountHealthCard summary={data.accounts} />
            <InventoryAlerts alerts={data.inventory_alerts} />
          </>
        ) : null}
      </div>

      {/* Right panel: chat */}
      <div className="w-96 flex-shrink-0 p-6">
        <ChatInterface />
      </div>
    </div>
  );
}
