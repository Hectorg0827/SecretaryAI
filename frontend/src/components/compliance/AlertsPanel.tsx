import React from 'react';
import clsx from 'clsx';
import { ShieldCheck, ExternalLink } from 'lucide-react';
import { ComplianceAlert } from '../../lib/api';

interface AlertsPanelProps {
  alerts: ComplianceAlert[];
  loading: boolean;
}

const PRIORITY_ORDER: ComplianceAlert['priority'][] = ['critical', 'warning', 'info'];

const PRIORITY_BADGE_STYLES: Record<ComplianceAlert['priority'], string> = {
  critical: 'bg-red-100 text-red-700',
  warning:  'bg-amber-100 text-amber-700',
  info:     'bg-blue-100 text-blue-700',
};

const PRIORITY_LABELS: Record<ComplianceAlert['priority'], string> = {
  critical: 'Critical',
  warning:  'Warning',
  info:     'Info',
};

const PRIORITY_BORDER: Record<ComplianceAlert['priority'], string> = {
  critical: 'border-l-red-500',
  warning:  'border-l-amber-400',
  info:     'border-l-blue-400',
};

function daysColor(days: number): string {
  if (days <= 0) return 'text-red-600 font-semibold';
  if (days <= 7) return 'text-red-600 font-semibold';
  if (days <= 30) return 'text-amber-600 font-medium';
  return 'text-slate-600';
}

export function AlertsPanel({ alerts, loading }: AlertsPanelProps) {
  if (loading) {
    return (
      <div className="space-y-2 p-6">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-14 bg-slate-100 rounded-lg animate-pulse" />
        ))}
      </div>
    );
  }

  if (alerts.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-slate-400 gap-3">
        <div className="w-14 h-14 rounded-2xl bg-slate-100 border border-slate-200 flex items-center justify-center">
          <ShieldCheck className="w-6 h-6 text-slate-300" />
        </div>
        <div className="text-center">
          <p className="text-sm font-medium text-slate-500">No compliance alerts</p>
          <p className="text-xs mt-1 text-slate-400">All licenses and registrations are current</p>
        </div>
      </div>
    );
  }

  const grouped = PRIORITY_ORDER.reduce<Record<ComplianceAlert['priority'], ComplianceAlert[]>>(
    (acc, p) => {
      acc[p] = alerts.filter((a) => a.priority === p);
      return acc;
    },
    { critical: [], warning: [], info: [] },
  );

  const sortedAlerts = [
    ...grouped.critical,
    ...grouped.warning,
    ...grouped.info,
  ];

  return (
    <div>
      {/* Toolbar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-slate-100 bg-slate-50">
        <p className="text-sm text-slate-500">
          {alerts.length} alert{alerts.length !== 1 ? 's' : ''}
          {grouped.critical.length > 0 && (
            <span className="ml-2 text-red-600 font-medium">
              · {grouped.critical.length} critical
            </span>
          )}
          {grouped.warning.length > 0 && (
            <span className="ml-2 text-amber-600 font-medium">
              · {grouped.warning.length} warning
            </span>
          )}
        </p>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-white border-b border-slate-100">
            <tr>
              {['Priority', 'State', 'Item', 'Days Until', 'Expiry Date', 'Action Required', 'Link'].map((h) => (
                <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {sortedAlerts.map((alert) => (
              <tr
                key={alert.id}
                className={clsx(
                  'hover:bg-slate-50 transition-colors border-l-4',
                  PRIORITY_BORDER[alert.priority],
                )}
              >
                <td className="px-5 py-3.5">
                  <span className={clsx(
                    'inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold',
                    PRIORITY_BADGE_STYLES[alert.priority],
                  )}>
                    {PRIORITY_LABELS[alert.priority]}
                  </span>
                </td>
                <td className="px-5 py-3.5">
                  <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-slate-100 text-slate-700 text-xs font-mono font-semibold">
                    {alert.state_code}
                  </span>
                </td>
                <td className="px-5 py-3.5">
                  <div className="text-sm font-semibold text-slate-800">{alert.item_name}</div>
                  <div className="text-xs text-slate-400 mt-0.5">{alert.alert_type}</div>
                </td>
                <td className={clsx('px-5 py-3.5 text-sm', daysColor(alert.days_until))}>
                  {alert.days_until <= 0 ? 'Expired' : `${alert.days_until}d`}
                </td>
                <td className="px-5 py-3.5 text-sm text-slate-600">
                  {alert.expiry_date}
                </td>
                <td className="px-5 py-3.5 text-sm text-slate-600 max-w-[260px]">
                  <span className="truncate block" title={alert.action_required}>
                    {alert.action_required}
                  </span>
                </td>
                <td className="px-5 py-3.5">
                  {alert.renewal_url && (
                    <a
                      href={alert.renewal_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-blue-600 text-white hover:bg-blue-700 transition-colors"
                    >
                      <ExternalLink size={11} />
                      Renew
                    </a>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
