import React, { useCallback, useEffect, useState } from 'react';
import { TopBar } from '../components/layout/TopBar';
import { StatStrip } from '../components/dashboard/StatStrip';
import { EmailInbox } from '../components/dashboard/EmailInbox';
import { SalesChart } from '../components/dashboard/SalesChart';
import { FollowUpNotes } from '../components/dashboard/FollowUpNotes';
import { AccountHealthPanel } from '../components/dashboard/AccountHealthPanel';
import { InventoryPanel } from '../components/dashboard/InventoryPanel';
import { PendingApprovals } from '../components/dashboard/PendingApprovals';
import { WorkflowStatusPanel } from '../components/dashboard/WorkflowStatusPanel';
import { ProactiveFeed } from '../components/dashboard/ProactiveFeed';
import { MorningBriefing } from '../components/dashboard/MorningBriefing';
import { ChatDrawer } from '../components/chat/ChatDrawer';
import { SkeletonCard } from '../components/ui/Skeleton';
import { api, DashboardSummary } from '../lib/api';
import { useAuth } from '../hooks/useAuth';

export function Dashboard({ onUnreadChange }: { onUnreadChange?: (n: number) => void }) {
  const [summary, setSummary]       = useState<DashboardSummary | null>(null);
  const [loading, setLoading]       = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError]   = useState(false);
  const { role, hasPermission, canApprove, canAccessChat } = useAuth();

  const loadSummary = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const s = await api.dashboard.summary();
      setSummary(s);
      onUnreadChange?.(s.unread_emails ?? 0);
    } catch {
      setSummary(null);
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [onUnreadChange]);

  useEffect(() => { void loadSummary(); }, [loadSummary]);

  const handleRefresh = () => {
    // This is intentionally a READ ONLY reload. POST /dashboard/refresh
    // currently schedules a morning-briefing worker and is not safe for UI refresh.
    setRefreshing(true);
    void loadSummary().finally(() => setRefreshing(false));
  };

  const accounts = summary?.accounts  ?? { healthy: 0, slowing: 0, at_risk: 0, dormant: 0 };
  const alerts   = summary?.inventory_alerts ?? [];

  const showEmailInbox    = hasPermission('trigger_actions');   // owner, manager
  const showSalesChart    = hasPermission('view_financials');   // owner, manager
  const showAccountHealth = hasPermission('view_all') || hasPermission('view_customers'); // owner, manager, back_office
  const showInventory     = hasPermission('view_inventory') || hasPermission('view_all'); // all except viewer
  const showApprovals     = canApprove;                         // owner, manager
  const showNotes         = hasPermission('view_all'); // per-rep notes lack a scoped backend contract

  // Build stat tiles based on what this role can see
  const statItems = summary ? [
    ...(showEmailInbox    ? [{ label: 'Unread Emails',    value: summary.unread_emails ?? 'Unavailable',     accent: 'blue'    as const }] : []),
    ...(showApprovals     ? [{ label: 'Pending Approvals',value: summary.pending_actions ?? 'Unavailable',   accent: 'amber'   as const }] : []),
    ...(showAccountHealth ? [{ label: 'Accounts At Risk', value: accounts.at_risk + accounts.dormant,      accent: 'red'     as const }] : []),
    ...(showInventory     ? [{ label: 'Inventory Alerts', value: alerts.length,                            accent: 'amber'   as const }] : []),
  ] : [];

  // Determine layout: use two-column layout only if there's left-column content
  const hasLeftColumn = showEmailInbox || showSalesChart;

  return (
    <div className="flex flex-col h-full overflow-hidden bg-slate-50">
      {/* Top bar */}
      <TopBar />

      {/* Cache freshness banner */}
      {!loading && summary && (
        <div className="px-6 pt-3 flex items-center gap-2 text-xs text-slate-400">
          {summary.generated_at ? (
            <span>
              Data as of{' '}
              {new Date(summary.generated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          ) : (
            <span>Snapshot timestamp unavailable — freshness unverified</span>
          )}
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="ml-1 underline hover:text-slate-600 disabled:opacity-50"
          >
            {refreshing ? 'Reloading…' : 'Reload view'}
          </button>
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
      <div className="flex-1 overflow-y-auto px-4 sm:px-6 pb-6 pt-4">
        {loadError && <div role="alert" className="mb-4 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
          Dashboard summary unavailable. The absence of figures is not evidence of zero activity.
          <button onClick={handleRefresh} className="ml-2 underline font-semibold" disabled={refreshing}>Try again</button>
        </div>}
        {role === 'sales_rep' && <div role="status" className="mb-4 rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900">
          Your assigned customer portfolio is awaiting server-side assignment verification. Account details and private follow-ups remain unavailable until access can be scoped safely.
        </div>}
        {(role === 'owner' || role === 'manager') && (
          <div className="mb-5">
            <MorningBriefing summary={summary} summaryLoading={loading} />
          </div>
        )}
        {hasLeftColumn ? (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 min-h-0">
            {/* Column 1: Email + Sales */}
            <div className="lg:col-span-2 space-y-4">
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
                  {summary && showAccountHealth && <AccountHealthPanel summary={accounts} />}
                  <ProactiveFeed />
                  {summary && showInventory     && <InventoryPanel alerts={alerts} />}
                  {showApprovals     && <PendingApprovals />}
                  {showApprovals     && <WorkflowStatusPanel />}
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
                {summary && showAccountHealth && <AccountHealthPanel summary={accounts} />}
                {summary && showInventory     && <InventoryPanel alerts={alerts} />}
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
