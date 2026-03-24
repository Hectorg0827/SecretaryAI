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
import { useAuth } from '../hooks/useAuth';

export function Dashboard({ onUnreadChange }: { onUnreadChange?: (n: number) => void }) {
  const [summary, setSummary]       = useState<DashboardSummary | null>(null);
  const [loading, setLoading]       = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const { hasPermission, canApprove, canAccessChat } = useAuth();

  const loadSummary = () => {
    setLoading(true);
    api.dashboard.summary()
      .then((s) => {
        setSummary(s);
        onUnreadChange?.(s.unread_emails ?? 0);
      })
      .catch(() => setSummary(null))
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadSummary(); }, []);

  const handleRefresh = () => {
    setRefreshing(true);
    api.dashboard.refresh()
      .then(() => {
        // Re-fetch after a short delay so the worker has time to store the snapshot
        setTimeout(loadSummary, 3000);
      })
      .catch(() => {})
      .finally(() => setRefreshing(false));
  };

  const accounts = summary?.accounts  ?? { healthy: 0, slowing: 0, at_risk: 0, dormant: 0 };
  const alerts   = summary?.inventory_alerts ?? [];

  const showEmailInbox    = hasPermission('trigger_actions');   // owner, manager
  const showSalesChart    = hasPermission('view_financials');   // owner, manager
  const showAccountHealth = hasPermission('view_all') || hasPermission('view_customers'); // owner, manager, back_office
  const showInventory     = hasPermission('view_inventory') || hasPermission('view_all'); // all except viewer
  const showApprovals     = canApprove;                         // owner, manager
  const showNotes         = hasPermission('view_own_accounts') || hasPermission('view_all'); // owner, manager, sales_rep

  // Build stat tiles based on what this role can see
  const statItems = summary ? [
    ...(showEmailInbox    ? [{ label: 'Unread Emails',    value: summary.unread_emails,                    accent: 'blue'    as const }] : []),
    ...(showApprovals     ? [{ label: 'Pending Approvals',value: summary.pending_actions,                  accent: 'amber'   as const }] : []),
    ...(showAccountHealth ? [{ label: 'Accounts At Risk', value: accounts.at_risk + accounts.dormant,      accent: 'red'     as const }] : []),
    ...(showInventory     ? [{ label: 'Inventory Alerts', value: alerts.length,                            accent: 'amber'   as const }] : []),
  ] : [];

  // Determine layout: use two-column layout only if there's left-column content
  const hasLeftColumn = showEmailInbox || showSalesChart;

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-slate-50">
      {/* Top bar */}
      <TopBar unreadCount={summary?.unread_emails ?? 0} />

      {/* Cache freshness banner */}
      {!loading && summary && (
        <div className="px-6 pt-3 flex items-center gap-2 text-xs text-slate-400">
          {summary.generated_at ? (
            <span>
              Data as of{' '}
              {new Date(summary.generated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          ) : (
            <span>Live data</span>
          )}
          {hasPermission('run_reports') && (
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="ml-1 underline hover:text-slate-600 disabled:opacity-50"
            >
              {refreshing ? 'Refreshing…' : 'Refresh'}
            </button>
          )}
        </div>
      )}

      {/* KPI strip */}
      {loading ? (
        <div className="px-6 pt-4">
          <div className="h-20 bg-white rounded-xl border border-slate-100 animate-pulse" />
        </div>
      ) : statItems.length > 0 ? (
        <div className="px-6 pt-4">
          <StatStrip stats={statItems} />
        </div>
      ) : null}

      {/* Main grid */}
      <div className="flex-1 overflow-y-auto px-6 pb-6 pt-4">
        {hasLeftColumn ? (
          <div className="grid grid-cols-3 gap-4 min-h-0">
            {/* Column 1: Email + Sales */}
            <div className="col-span-2 space-y-4">
              {showEmailInbox && <EmailInbox />}
              {showSalesChart && <SalesChart />}
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
                  {showAccountHealth && <AccountHealthPanel summary={accounts} />}
                  {showInventory     && <InventoryPanel alerts={alerts} />}
                  {showApprovals     && <PendingApprovals />}
                  {showNotes         && <FollowUpNotes />}
                </>
              )}
            </div>
          </div>
        ) : (
          /* Reduced layout for sales_rep / back_office / viewer — single column */
          <div className="max-w-xl space-y-4">
            {loading ? (
              <>
                <SkeletonCard lines={4} />
                <SkeletonCard lines={4} />
              </>
            ) : (
              <>
                {showAccountHealth && <AccountHealthPanel summary={accounts} />}
                {showInventory     && <InventoryPanel alerts={alerts} />}
                {showNotes         && <FollowUpNotes />}
              </>
            )}
          </div>
        )}
      </div>

      {/* Floating live chat drawer — hidden for viewer */}
      {canAccessChat && <ChatDrawer />}
    </div>
  );
}
