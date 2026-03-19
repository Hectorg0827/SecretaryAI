import React, { useState } from 'react';
import clsx from 'clsx';
import { TrendingUp, TrendingDown, AlertTriangle, Clock, ChevronRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Card, CardHeader } from '../ui/Card';

interface Summary { healthy: number; slowing: number; at_risk: number; dormant: number; }

const STATUS_CFG = {
  healthy: { label: 'Healthy',  icon: TrendingUp,    bar: 'bg-emerald-500', text: 'text-emerald-700', bg: 'bg-emerald-50' },
  slowing: { label: 'Slowing',  icon: TrendingDown,  bar: 'bg-amber-400',   text: 'text-amber-700',   bg: 'bg-amber-50'  },
  at_risk: { label: 'At Risk',  icon: AlertTriangle, bar: 'bg-orange-500',  text: 'text-orange-700',  bg: 'bg-orange-50' },
  dormant: { label: 'Dormant',  icon: Clock,         bar: 'bg-red-500',     text: 'text-red-700',     bg: 'bg-red-50'    },
} as const;

export function AccountHealthPanel({ summary }: { summary: Summary }) {
  const navigate = useNavigate();
  const total = Object.values(summary).reduce((a, b) => a + b, 0);

  return (
    <Card>
      <CardHeader
        title="Account Health"
        count={total}
        action={
          <button
            onClick={() => navigate('/accounts')}
            className="flex items-center gap-0.5 text-xs text-blue-600 hover:text-blue-700 font-medium"
          >
            View all <ChevronRight size={12} />
          </button>
        }
      />

      {/* Segmented progress bar */}
      <div className="px-5 pt-4 pb-2">
        <div className="flex rounded-full overflow-hidden h-2 gap-px">
          {(Object.keys(STATUS_CFG) as Array<keyof typeof STATUS_CFG>).map((k) => {
            const pct = total ? (summary[k] / total) * 100 : 0;
            return pct > 0 ? (
              <div key={k} style={{ width: `${pct}%` }} className={STATUS_CFG[k].bar} />
            ) : null;
          })}
        </div>
      </div>

      {/* Status rows */}
      <div className="px-5 pb-4 space-y-2">
        {(Object.keys(STATUS_CFG) as Array<keyof typeof STATUS_CFG>).map((k) => {
          const cfg  = STATUS_CFG[k];
          const Icon = cfg.icon;
          const n    = summary[k];
          const pct  = total ? Math.round((n / total) * 100) : 0;
          return (
            <button
              key={k}
              onClick={() => navigate(`/accounts?filter=${k}`)}
              className="w-full flex items-center gap-3 hover:bg-slate-50 rounded-lg px-2 py-1.5 transition-colors group"
            >
              <div className={clsx('w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0', cfg.bg)}>
                <Icon size={12} className={cfg.text} />
              </div>
              <span className="text-sm text-slate-600 flex-1 text-left">{cfg.label}</span>
              <span className={clsx('text-sm font-bold', cfg.text)}>{n}</span>
              <span className="text-xs text-slate-400 w-8 text-right">{pct}%</span>
              <ChevronRight size={12} className="text-slate-300 group-hover:text-slate-500 transition-colors" />
            </button>
          );
        })}
      </div>
    </Card>
  );
}
