import React from 'react';
import clsx from 'clsx';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';

interface StatItem {
  label: string;
  value: string | number;
  sub?: string;
  trend?: number;   // % change vs prior period
  accent?: 'blue' | 'emerald' | 'amber' | 'red';
}

const ACCENT_CLS = {
  blue:    'text-blue-600',
  emerald: 'text-emerald-600',
  amber:   'text-amber-600',
  red:     'text-red-600',
};

function Trend({ pct }: { pct: number }) {
  if (pct > 0)  return <span className="flex items-center gap-0.5 text-emerald-600 text-xs font-medium"><TrendingUp  size={11} />+{pct.toFixed(0)}%</span>;
  if (pct < 0)  return <span className="flex items-center gap-0.5 text-red-500    text-xs font-medium"><TrendingDown size={11} />{pct.toFixed(0)}%</span>;
  return               <span className="flex items-center gap-0.5 text-slate-400  text-xs font-medium"><Minus         size={11} />0%</span>;
}

export function StatStrip({ stats }: { stats: StatItem[] }) {
  return (
    <div className="grid grid-cols-4 gap-4">
      {stats.map((s) => (
        <div key={s.label} className="bg-white rounded-xl border border-slate-200 shadow-card px-5 py-4">
          <div className="text-xs font-medium text-slate-400 uppercase tracking-wide mb-1">{s.label}</div>
          <div className={clsx('text-2xl font-bold text-slate-900', s.accent && ACCENT_CLS[s.accent])}>
            {s.value}
          </div>
          <div className="flex items-center gap-2 mt-1">
            {s.sub && <span className="text-xs text-slate-400">{s.sub}</span>}
            {s.trend !== undefined && <Trend pct={s.trend} />}
          </div>
        </div>
      ))}
    </div>
  );
}
