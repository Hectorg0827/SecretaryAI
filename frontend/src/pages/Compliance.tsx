import React, { useEffect, useState } from 'react';
import clsx from 'clsx';
import {
  Scale, Bell, CalendarClock, FileText, Search, CheckCircle2, AlertCircle, AlertTriangle,
} from 'lucide-react';
import {
  api,
  ComplianceStatus,
  ComplianceAlert,
  ComplianceDeadline,
  StateLicense,
  BrandRegistration,
  FederalPermit,
} from '../lib/api';
import { AlertsPanel }            from '../components/compliance/AlertsPanel';
import { DeadlinesPanel }         from '../components/compliance/DeadlinesPanel';
import { ShipmentChecker }        from '../components/compliance/ShipmentChecker';
import { LicenseRegistry }        from '../components/compliance/LicenseRegistry';
import { ComplianceStatusBadge }  from '../components/compliance/ComplianceStatusBadge';

// ─── Tab definitions ──────────────────────────────────────────────────────────

type TabId = 'status' | 'alerts' | 'deadlines' | 'licenses' | 'check';

const TABS: { id: TabId; label: string; icon: React.ElementType }[] = [
  { id: 'status',    label: 'Status',          icon: Scale        },
  { id: 'alerts',    label: 'Alerts',          icon: Bell         },
  { id: 'deadlines', label: 'Deadlines',       icon: CalendarClock },
  { id: 'licenses',  label: 'Licenses',        icon: FileText     },
  { id: 'check',     label: 'Check Shipment',  icon: Search       },
];

// ─── State status grid ────────────────────────────────────────────────────────

function StateStatusGrid({ status }: { status: ComplianceStatus }) {
  const entries = Object.entries(status.state_status);
  if (entries.length === 0) {
    return (
      <div className="py-8 text-center text-slate-400 text-sm">
        No active states. Add state licenses to begin tracking compliance.
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2 mt-4">
      {entries.map(([state, health]) => {
        const icon =
          health === 'critical' ? <AlertCircle   size={12} className="text-red-500" />
          : health === 'warning' ? <AlertTriangle size={12} className="text-amber-500" />
          : <CheckCircle2 size={12} className="text-emerald-500" />;
        const bg =
          health === 'critical' ? 'bg-red-50 border-red-200'
          : health === 'warning' ? 'bg-amber-50 border-amber-200'
          : 'bg-emerald-50 border-emerald-200';
        return (
          <div key={state} className={clsx('flex items-center gap-1.5 px-3 py-2 rounded-lg border text-sm font-medium', bg)}>
            {icon}
            {state}
          </div>
        );
      })}
    </div>
  );
}

// ─── Summary stat card ────────────────────────────────────────────────────────

function StatCard({ label, value, accent }: { label: string; value: number | string; accent?: string }) {
  return (
    <div className="bg-slate-50 rounded-xl p-4 text-center">
      <div className={clsx('text-2xl font-bold', accent ?? 'text-slate-800')}>{value}</div>
      <div className="text-xs text-slate-400 mt-0.5">{label}</div>
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export function Compliance() {
  const [activeTab, setActiveTab] = useState<TabId>('status');

  // Data state
  const [status, setStatus]               = useState<ComplianceStatus | null>(null);
  const [alerts, setAlerts]               = useState<ComplianceAlert[]>([]);
  const [deadlines, setDeadlines]         = useState<ComplianceDeadline[]>([]);
  const [licenses, setLicenses]           = useState<StateLicense[]>([]);
  const [brandRegs, setBrandRegs]         = useState<BrandRegistration[]>([]);
  const [federalPermits, setFederalPermits] = useState<FederalPermit[]>([]);

  const [loadingStatus,   setLoadingStatus]   = useState(false);
  const [loadingAlerts,   setLoadingAlerts]   = useState(false);
  const [loadingDeadlines, setLoadingDeadlines] = useState(false);
  const [loadingLicenses, setLoadingLicenses] = useState(false);

  // Load on mount
  useEffect(() => {
    setLoadingStatus(true);
    api.compliance.getStatus()
      .then(setStatus)
      .catch(() => null)
      .finally(() => setLoadingStatus(false));

    setLoadingAlerts(true);
    api.compliance.getAlerts()
      .then(setAlerts)
      .catch(() => [])
      .finally(() => setLoadingAlerts(false));

    setLoadingDeadlines(true);
    api.compliance.getDeadlines()
      .then(setDeadlines)
      .catch(() => [])
      .finally(() => setLoadingDeadlines(false));

    setLoadingLicenses(true);
    Promise.all([
      api.compliance.getLicenses(),
      api.compliance.getBrandRegistrations(),
      api.compliance.getFederalPermits(),
    ])
      .then(([lics, brands, fed]) => {
        setLicenses(lics as StateLicense[]);
        setBrandRegs(brands as BrandRegistration[]);
        setFederalPermits(fed as FederalPermit[]);
      })
      .catch(() => null)
      .finally(() => setLoadingLicenses(false));
  }, []);

  const criticalCount = status?.critical_alerts ?? 0;
  const warningCount  = status?.warning_alerts ?? 0;

  const tabBadge = (id: TabId): number => {
    if (id === 'alerts')    return alerts.length;
    if (id === 'deadlines') return deadlines.length;
    if (id === 'licenses')  return licenses.length + brandRegs.length + federalPermits.length;
    return 0;
  };

  return (
    <div className="flex flex-col h-screen bg-slate-50">
      {/* Page header */}
      <div className="bg-white border-b border-slate-100 px-6 py-4 flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-indigo-50 flex items-center justify-center">
            <Scale size={18} className="text-indigo-600" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-slate-900">Compliance Manager</h1>
            <p className="text-sm text-slate-400 mt-0.5">
              Licenses · COLA · Brand registrations · Pre-shipment checks · 50-state rules
            </p>
          </div>

          {/* Quick stats */}
          <div className="ml-auto flex items-center gap-3">
            {status && (
              <ComplianceStatusBadge status={status.overall} />
            )}
            {criticalCount > 0 && (
              <div className="flex items-center gap-1 text-red-600 text-sm font-semibold bg-red-50 px-3 py-1.5 rounded-lg">
                <AlertCircle size={14} />
                {criticalCount} critical
              </div>
            )}
            {warningCount > 0 && (
              <div className="flex items-center gap-1 text-amber-600 text-sm font-semibold bg-amber-50 px-3 py-1.5 rounded-lg">
                <AlertTriangle size={14} />
                {warningCount} warning
              </div>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mt-4">
          {TABS.map(({ id, label, icon: Icon }) => {
            const badge = tabBadge(id);
            return (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className={clsx(
                  'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors',
                  activeTab === id
                    ? 'bg-slate-800 text-white'
                    : 'text-slate-500 hover:bg-slate-100 hover:text-slate-700',
                )}
              >
                <Icon size={14} />
                {label}
                {badge > 0 && (
                  <span className={clsx(
                    'px-1.5 py-0.5 rounded-full text-[10px] font-bold min-w-[18px] text-center leading-none',
                    activeTab === id
                      ? 'bg-white/20 text-white'
                      : id === 'alerts' && criticalCount > 0
                        ? 'bg-red-100 text-red-600'
                        : 'bg-slate-200 text-slate-600',
                  )}>
                    {badge}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-auto">
        <div className="bg-white min-h-full shadow-sm">

          {/* Status tab */}
          {activeTab === 'status' && (
            <div className="p-6">
              {loadingStatus ? (
                <div className="space-y-3">
                  {[1, 2, 3].map((i) => (
                    <div key={i} className="h-12 bg-slate-100 rounded-lg animate-pulse" />
                  ))}
                </div>
              ) : status ? (
                <>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
                    <StatCard label="Active States" value={status.active_states} />
                    <StatCard label="Products" value={status.total_products} />
                    <StatCard
                      label="Critical Alerts"
                      value={status.critical_alerts}
                      accent={status.critical_alerts > 0 ? 'text-red-600' : undefined}
                    />
                    <StatCard
                      label="Due in 7 Days"
                      value={status.deadlines_due_7_days}
                      accent={status.deadlines_due_7_days > 0 ? 'text-amber-600' : undefined}
                    />
                  </div>
                  <h2 className="text-sm font-semibold text-slate-700 uppercase tracking-wider mb-2">
                    State-by-State Status
                  </h2>
                  <StateStatusGrid status={status} />
                </>
              ) : (
                <div className="py-12 text-center text-slate-400 text-sm">
                  No compliance data yet. Add licenses and products to get started.
                </div>
              )}
            </div>
          )}

          {/* Alerts tab */}
          {activeTab === 'alerts' && (
            <AlertsPanel alerts={alerts} loading={loadingAlerts} />
          )}

          {/* Deadlines tab */}
          {activeTab === 'deadlines' && (
            <DeadlinesPanel deadlines={deadlines} loading={loadingDeadlines} />
          )}

          {/* Licenses tab */}
          {activeTab === 'licenses' && (
            <LicenseRegistry
              licenses={licenses}
              brandRegistrations={brandRegs}
              federalPermits={federalPermits}
              loading={loadingLicenses}
            />
          )}

          {/* Check Shipment tab */}
          {activeTab === 'check' && <ShipmentChecker />}
        </div>
      </div>
    </div>
  );
}
