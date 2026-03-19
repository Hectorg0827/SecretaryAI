import React from 'react';
import clsx from 'clsx';
import { TrendingUp, TrendingDown, AlertTriangle, Clock } from 'lucide-react';

interface AccountHealthSummary {
  healthy: number;
  slowing: number;
  at_risk: number;
  dormant: number;
}

interface Props {
  summary: AccountHealthSummary;
}

const STATUS_CONFIG = {
  healthy: { label: 'Healthy', color: 'text-green-600 bg-green-50', icon: TrendingUp },
  slowing: { label: 'Slowing', color: 'text-yellow-600 bg-yellow-50', icon: TrendingDown },
  at_risk: { label: 'At Risk', color: 'text-orange-600 bg-orange-50', icon: AlertTriangle },
  dormant: { label: 'Dormant', color: 'text-red-600 bg-red-50', icon: Clock },
} as const;

export function AccountHealthCard({ summary }: Props) {
  const total = Object.values(summary).reduce((a, b) => a + b, 0);

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-5">
      <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">
        Account Health
      </h2>
      <div className="text-3xl font-bold text-gray-900 mb-1">{total}</div>
      <div className="text-sm text-gray-400 mb-4">Total accounts</div>

      <div className="grid grid-cols-2 gap-3">
        {(Object.keys(STATUS_CONFIG) as Array<keyof typeof STATUS_CONFIG>).map((status) => {
          const config = STATUS_CONFIG[status];
          const count = summary[status];
          const Icon = config.icon;
          return (
            <div key={status} className={clsx('rounded-lg px-3 py-2 flex items-center gap-2', config.color)}>
              <Icon size={14} />
              <div>
                <div className="font-bold text-lg leading-none">{count}</div>
                <div className="text-xs opacity-75">{config.label}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
