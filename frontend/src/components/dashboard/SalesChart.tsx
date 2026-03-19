import React, { useEffect, useState } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { TrendingUp, TrendingDown } from 'lucide-react';
import { api, SalesSummary } from '../../lib/api';
import { Card, CardHeader } from '../ui/Card';
import { SkeletonCard } from '../ui/Skeleton';

function fmt(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `$${(n / 1_000).toFixed(0)}k`;
  return `$${n.toFixed(0)}`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

interface StatProps { label: string; value: string; trend?: number; }
function MiniStat({ label, value, trend }: StatProps) {
  return (
    <div className="text-center">
      <div className="text-lg font-bold text-slate-900">{value}</div>
      <div className="text-xs text-slate-400 mt-0.5">{label}</div>
      {trend !== undefined && (
        <div className={`flex items-center justify-center gap-0.5 text-xs font-medium mt-0.5 ${trend >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
          {trend >= 0 ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
          {trend >= 0 ? '+' : ''}{trend.toFixed(0)}%
        </div>
      )}
    </div>
  );
}

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-slate-900 text-white text-xs rounded-lg px-3 py-2 shadow-lg">
      <div className="font-medium mb-1">{label}</div>
      <div className="text-blue-300">{fmt(payload[0]?.value ?? 0)} revenue</div>
      <div className="text-slate-400">{payload[1]?.value ?? 0} orders</div>
    </div>
  );
};

export function SalesChart() {
  const [data,    setData]    = useState<SalesSummary | null>(null);
  const [days,    setDays]    = useState(30);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.dashboard.sales(days)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [days]);

  if (loading) return <SkeletonCard lines={5} />;
  if (!data)   return null;

  const chartData = (data.chart_data ?? []).map((d) => ({
    ...d,
    dateLabel: formatDate(d.date),
  }));

  return (
    <Card>
      <CardHeader
        title="Sales Analytics"
        action={
          <div className="flex rounded-lg border border-slate-200 overflow-hidden text-xs">
            {([7, 30, 90] as const).map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`px-2.5 py-1 font-medium transition-colors ${days === d ? 'bg-slate-800 text-white' : 'text-slate-500 hover:bg-slate-50'}`}
              >
                {d}d
              </button>
            ))}
          </div>
        }
      />

      {/* Mini stats row */}
      <div className="grid grid-cols-4 divide-x divide-slate-100 border-b border-slate-100">
        <div className="px-5 py-3">
          <MiniStat label="Revenue" value={fmt(data.total_revenue)} trend={data.vs_prior_period_pct} />
        </div>
        <div className="px-5 py-3">
          <MiniStat label="Orders" value={String(data.order_count)} />
        </div>
        <div className="px-5 py-3">
          <MiniStat label="Avg Order" value={fmt(data.avg_order_value)} />
        </div>
        <div className="px-5 py-3 text-center">
          <div className="text-sm font-semibold text-slate-900 truncate">{data.top_account || '—'}</div>
          <div className="text-xs text-slate-400 mt-0.5">Top account</div>
        </div>
      </div>

      {/* Chart */}
      <div className="px-4 pt-4 pb-2" style={{ height: 180 }}>
        {chartData.length === 0 ? (
          <div className="flex items-center justify-center h-full text-slate-400 text-sm">
            No sales data for this period
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="salesGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.15} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}    />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
              <XAxis
                dataKey="dateLabel"
                tick={{ fontSize: 10, fill: '#94a3b8' }}
                tickLine={false}
                axisLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={{ fontSize: 10, fill: '#94a3b8' }}
                tickFormatter={(v) => fmt(v)}
                tickLine={false}
                axisLine={false}
                width={48}
              />
              <Tooltip content={<CustomTooltip />} />
              <Area
                type="monotone"
                dataKey="revenue"
                stroke="#3b82f6"
                strokeWidth={2}
                fill="url(#salesGrad)"
                dot={false}
                activeDot={{ r: 4, fill: '#3b82f6' }}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </Card>
  );
}
