import React, { useEffect, useState } from 'react';
import { TopBar } from '../components/layout/TopBar';
import { StatStrip } from '../components/dashboard/StatStrip';
import { EmailInbox } from '../components/dashboard/EmailInbox';
import { SalesChart } from '../components/dashboard/SalesChart';
import { FollowUpNotes } from '../components/dashboard/FollowUpNotes';
import { AccountHealthPanel } from '../components/dashboard/AccountHealthPanel';
import { InventoryPanel } from '../components/dashboard/InventoryPanel';
import { PendingApprovals } from '../components/dashboard/PendingApprovals';
import { ChatDrawer } from '../components/chat/ChatDrawer';
import { SkeletonCard } from '../components/ui/Skeleton';
import { api, DashboardSummary } from '../lib/api';

export function Dashboard({ onUnreadChange }: { onUnreadChange?: (n: number) => void }) {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading]  = useState(true);

  useEffect(() => {
    api.dashboard.summary()
      .then((s) => {
        setSummary(s);
        onUnreadChange?.(s.unread_emails ?? 0);
      })
      .catch(() => setSummary(null))
      .finally(() => setLoading(false));
  }, []);

  const accounts = summary?.accounts  ?? { healthy: 0, slowing: 0, at_risk: 0, dormant: 0 };
  const alerts   = summary?.inventory_alerts ?? [];

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-slate-50">
      {/* Top bar */}
      <TopBar unreadCount={summary?.unread_emails ?? 0} />

      {/* KPI strip */}
      {loading ? (
        <div className="px-6 pt-4">
          <div className="h-20 bg-white rounded-xl border border-slate-100 animate-pulse" />
        </div>
      ) : summary ? (
        <div className="px-6 pt-4">
          <StatStrip
            unreadEmails={summary.unread_emails}
            pendingActions={summary.pending_actions}
            accountsAtRisk={accounts.at_risk + accounts.dormant}
            inventoryAlerts={alerts.length}
          />
        </div>
      ) : null}

      {/* Main grid */}
      <div className="flex-1 overflow-y-auto px-6 pb-6 pt-4">
        <div className="grid grid-cols-3 gap-4 min-h-0">

          {/* Column 1: Email + Sales */}
          <div className="col-span-2 space-y-4">
            <EmailInbox />
            <SalesChart />
          </div>

          {/* Column 2: Right widgets */}
          <div className="space-y-4">
            {loading ? (
              <>
                <SkeletonCard lines={4} />
                <SkeletonCard lines={4} />
                <SkeletonCard lines={3} />
              </>
            ) : (
              <>
                <AccountHealthPanel summary={accounts} />
                <InventoryPanel alerts={alerts} />
                <PendingApprovals />
                <FollowUpNotes />
              </>
            )}
          </div>
        </div>
      </div>

      {/* Floating live chat drawer */}
      <ChatDrawer />
    </div>
  );
}
