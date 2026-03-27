import React, { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import clsx from 'clsx';
import { TrendingUp, TrendingDown, AlertTriangle, Clock, Search, Users } from 'lucide-react';
import { api, Account } from '../lib/api';
import { Badge } from '../components/ui/Badge';
import { AccountDetail } from '../components/accounts/AccountDetail';

const HEALTH_CFG = {
  healthy: { label: 'Healthy', icon: TrendingUp,    color: 'text-emerald-600', bg: 'bg-emerald-50' },
  slowing: { label: 'Slowing', icon: TrendingDown,  color: 'text-amber-600',   bg: 'bg-amber-50'   },
  at_risk: { label: 'At Risk', icon: AlertTriangle,  color: 'text-orange-600',  bg: 'bg-orange-50'  },
  dormant: { label: 'Dormant', icon: Clock,           color: 'text-red-600',     bg: 'bg-red-50'     },
  unknown: { label: 'Unknown', icon: Users,            color: 'text-slate-500',   bg: 'bg-slate-50'   },
} as const;

const FILTERS = ['all', 'healthy', 'slowing', 'at_risk', 'dormant'] as const;

function fmt(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `$${(n / 1_000).toFixed(0)}k`;
  return `$${n.toFixed(0)}`;
}

function AccountRow({ account, onClick }: { account: Account; onClick: () => void }) {
  const cfg  = HEALTH_CFG[account.health_status] ?? HEALTH_CFG.unknown;
  const Icon = cfg.icon;

  return (
    <tr
      onClick={onClick}
      className="hover:bg-slate-50 transition-colors group cursor-pointer"
    >
      <td className="px-5 py-3.5">
        <div className="flex items-center gap-3">
          <div className={clsx('w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0', cfg.bg)}>
            <Icon size={14} className={cfg.color} />
          </div>
          <div>
            <div className="text-sm font-semibold text-slate-800">{account.name}</div>
            <div className="text-xs text-slate-400">{account.email}</div>
          </div>
        </div>
      </td>
      <td className="px-5 py-3.5">
        <Badge variant={account.health_status === 'unknown' ? 'low' : account.health_status as any} label={cfg.label} />
      </td>
      <td className="px-5 py-3.5 text-sm text-slate-600 font-medium">{fmt(account.current_balance)}</td>
      <td className="px-5 py-3.5 text-sm text-slate-600">{fmt(account.avg_order_value)}</td>
      <td className="px-5 py-3.5 text-sm text-slate-400">
        {account.last_order_date
          ? new Date(account.last_order_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
          : '—'}
      </td>
      <td className="px-5 py-3.5 text-xs text-slate-400">{account.assigned_rep ?? '—'}</td>
    </tr>
  );
}

export function Accounts() {
  const [accounts, setAccounts]           = useState<Account[]>([]);
  const [loading,  setLoading]            = useState(true);
  const [search,   setSearch]             = useState('');
  const [selectedAccount, setSelected]    = useState<Account | null>(null);
  const [params, setParams]               = useSearchParams();
  const filter = (params.get('filter') ?? 'all') as typeof FILTERS[number];

  useEffect(() => {
    api.accounts.list()
      .then((r) => setAccounts(r.accounts ?? []))
      .catch(() => setAccounts([]))
      .finally(() => setLoading(false));
  }, []);

  const filtered = accounts.filter((a) => {
    const matchFilter = filter === 'all' || a.health_status === filter;
    const matchSearch = !search || a.name.toLowerCase().includes(search.toLowerCase()) || a.email.toLowerCase().includes(search.toLowerCase());
    return matchFilter && matchSearch;
  });

  const counts = FILTERS.reduce((acc, f) => ({
    ...acc,
    [f]: f === 'all' ? accounts.length : accounts.filter((a) => a.health_status === f).length,
  }), {} as Record<string, number>);

  return (
    <div className="flex h-screen bg-slate-50 overflow-hidden">
    <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
      {/* Header */}
      <div className="bg-white border-b border-slate-100 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-slate-900">Accounts</h1>
            <p className="text-sm text-slate-400 mt-0.5">{accounts.length} total accounts</p>
          </div>
          {/* Search */}
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search accounts…"
              className="pl-8 pr-4 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white w-56"
            />
          </div>
        </div>

        {/* Filter tabs */}
        <div className="flex gap-1 mt-4">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setParams(f === 'all' ? {} : { filter: f })}
              className={clsx(
                'px-3 py-1.5 text-xs font-medium rounded-lg transition-colors capitalize',
                filter === f
                  ? 'bg-slate-800 text-white'
                  : 'text-slate-500 hover:bg-slate-100',
              )}
            >
              {f === 'all' ? 'All' : f.replace('_', ' ')} ({counts[f] ?? 0})
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {loading ? (
          <div className="flex items-center justify-center h-40 text-slate-400 text-sm">Loading…</div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 text-slate-400">
            <Users size={24} className="mb-2 opacity-40" />
            <p className="text-sm">No accounts match this filter</p>
          </div>
        ) : (
          <table className="w-full">
            <thead className="bg-white border-b border-slate-100 sticky top-0">
              <tr>
                {['Account', 'Health', 'Balance', 'Avg Order', 'Last Order', 'Rep'].map((h) => (
                  <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {filtered.map((a) => <AccountRow key={a.id} account={a} onClick={() => setSelected(a)} />)}
            </tbody>
          </table>
        )}
      </div>
    </div>

      {/* Account detail drawer */}
      {selectedAccount && (
        <div className="w-96 flex-shrink-0 border-l border-slate-200 overflow-hidden">
          <AccountDetail
            account={selectedAccount}
            onClose={() => setSelected(null)}
          />
        </div>
      )}
    </div>
  );
}
