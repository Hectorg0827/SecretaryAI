import React from 'react';
import clsx from 'clsx';
import { TrendingUp, TrendingDown, Minus, BarChart3 } from 'lucide-react';
import { SeverityBadge, Severity } from './SeverityBadge';

export interface CostVarianceRow {
  id: string;
  component: string;
  previous: number;
  current: number;
  change_pct: number;
  annualized_impact: number;
  severity: Severity;
  currency?: string;
}

interface CostAnalysisProps {
  rows: CostVarianceRow[];
  loading: boolean;
  totalLandedCostDelta?: number;
  currency?: string;
}

function fmtMoney(value: number, currency = 'USD'): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function ChangePct({ pct }: { pct: number }) {
  const abs = Math.abs(pct);
  if (pct > 0) {
    return (
      <span className="flex items-center gap-1 text-red-600 font-semibold text-sm">
        <TrendingUp size={13} />
        +{abs.toFixed(1)}%
      </span>
    );
  }
  if (pct < 0) {
    return (
      <span className="flex items-center gap-1 text-green-600 font-semibold text-sm">
        <TrendingDown size={13} />
        -{abs.toFixed(1)}%
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 text-slate-400 text-sm">
      <Minus size={13} />
      0%
    </span>
  );
}

export function CostAnalysis({ rows, loading, totalLandedCostDelta, currency = 'USD' }: CostAnalysisProps) {
  if (loading) {
    return (
      <div className="space-y-2 p-6">
        <div className="h-24 bg-slate-100 rounded-xl animate-pulse mb-4" />
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-14 bg-slate-100 rounded-lg animate-pulse" />
        ))}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-slate-400 gap-3">
        <div className="w-14 h-14 rounded-2xl bg-slate-100 border border-slate-200 flex items-center justify-center">
          <BarChart3 className="w-6 h-6 text-slate-300" />
        </div>
        <div className="text-center">
          <p className="text-sm font-medium text-slate-500">No cost data</p>
          <p className="text-xs mt-1 text-slate-400">Cost variance analysis will populate once freight invoices are reconciled</p>
        </div>
      </div>
    );
  }

  const totalImpact = totalLandedCostDelta ?? rows.reduce((s, r) => s + r.annualized_impact, 0);
  const alertCount = rows.filter((r) => r.severity === 'alert' || r.severity === 'critical').length;

  return (
    <div>
      {/* Summary card */}
      <div className="m-6 bg-white rounded-xl shadow-sm border border-slate-200 p-5">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Total Landed Cost Delta</p>
            <p className={clsx(
              'text-2xl font-bold',
              totalImpact > 0 ? 'text-red-600' : totalImpact < 0 ? 'text-green-600' : 'text-slate-700',
            )}>
              {totalImpact >= 0 ? '+' : ''}{fmtMoney(totalImpact, currency)}
            </p>
            <p className="text-xs text-slate-400 mt-1">Annualized impact vs prior period</p>
          </div>
          <div className="flex gap-4">
            <div className="text-center">
              <div className="text-lg font-bold text-slate-800">{rows.length}</div>
              <div className="text-xs text-slate-400">Components</div>
            </div>
            {alertCount > 0 && (
              <div className="text-center">
                <div className="text-lg font-bold text-orange-600">{alertCount}</div>
                <div className="text-xs text-slate-400">Alerts</div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-white border-b border-slate-100">
            <tr>
              {['Component', 'Previous', 'Current', 'Change %', 'Annualized Impact', 'Severity'].map((h) => (
                <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((row) => {
              const rowCurrency = row.currency ?? currency;
              return (
                <tr
                  key={row.id}
                  className={clsx(
                    'hover:bg-slate-50 transition-colors',
                    (row.severity === 'critical' || row.severity === 'alert') && 'bg-red-50/30',
                  )}
                >
                  <td className="px-5 py-3.5">
                    <span className="text-sm font-medium text-slate-800">{row.component}</span>
                  </td>
                  <td className="px-5 py-3.5 text-sm text-slate-600 font-mono">
                    {fmtMoney(row.previous, rowCurrency)}
                  </td>
                  <td className="px-5 py-3.5 text-sm text-slate-800 font-semibold font-mono">
                    {fmtMoney(row.current, rowCurrency)}
                  </td>
                  <td className="px-5 py-3.5">
                    <ChangePct pct={row.change_pct} />
                  </td>
                  <td className="px-5 py-3.5">
                    <span className={clsx(
                      'text-sm font-semibold font-mono',
                      row.annualized_impact > 0 ? 'text-red-600' :
                      row.annualized_impact < 0 ? 'text-green-600' : 'text-slate-500',
                    )}>
                      {row.annualized_impact >= 0 ? '+' : ''}{fmtMoney(row.annualized_impact, rowCurrency)}
                    </span>
                  </td>
                  <td className="px-5 py-3.5">
                    <SeverityBadge level={row.severity} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
