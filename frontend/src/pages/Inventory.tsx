import React, { useEffect, useState } from 'react';
import clsx from 'clsx';
import { Package, AlertTriangle, XCircle, ShoppingCart, Search, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api, InventoryAlert } from '../lib/api';
import { useAuth } from '../hooks/useAuth';

const STATUS_CFG = {
  out_of_stock: { label: 'Out of Stock', icon: XCircle,       border: 'border-l-red-500',    bg: 'bg-red-50',     text: 'text-red-700',    badge: 'bg-red-100 text-red-700'      },
  critical:     { label: 'Critical',     icon: AlertTriangle, border: 'border-l-orange-400', bg: 'bg-orange-50',  text: 'text-orange-700', badge: 'bg-orange-100 text-orange-700'  },
  low:          { label: 'Low',          icon: Package,       border: 'border-l-amber-400',  bg: 'bg-amber-50',   text: 'text-amber-700',  badge: 'bg-amber-100 text-amber-700'    },
} as const;

const FILTERS = ['all', 'out_of_stock', 'critical', 'low'] as const;
type FilterType = typeof FILTERS[number];

function InventoryRow({ alert, onDraftPO, canDraftPO }: { alert: InventoryAlert; onDraftPO: (id: string, name: string) => void; canDraftPO: boolean }) {
  const cfg  = STATUS_CFG[alert.stock_status];
  const Icon = cfg.icon;

  return (
    <tr className="hover:bg-slate-50 transition-colors">
      <td className="px-5 py-3.5">
        <div className="flex items-center gap-3">
          <div className={clsx('w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0', cfg.bg)}>
            <Icon size={14} className={cfg.text} />
          </div>
          <div>
            <div className="text-sm font-semibold text-slate-800">{alert.product_name}</div>
            <div className="text-xs text-slate-400 font-mono">{alert.item_id}</div>
          </div>
        </div>
      </td>
      <td className="px-5 py-3.5">
        <span className={clsx('text-xs font-semibold px-2 py-1 rounded-lg', cfg.badge)}>
          {cfg.label}
        </span>
      </td>
      <td className="px-5 py-3.5 text-sm font-medium text-slate-700">{alert.total_qty.toLocaleString()} units</td>
      <td className="px-5 py-3.5 text-sm text-slate-500">
        {alert.weeks_remaining != null ? `~${alert.weeks_remaining.toFixed(1)} wks` : '—'}
      </td>
      <td className="px-5 py-3.5">
        {alert.needs_po && canDraftPO ? (
          <button
            onClick={() => onDraftPO(alert.item_id, alert.product_name)}
            className="flex items-center gap-1.5 text-xs text-blue-600 hover:text-blue-700 border border-blue-200 hover:border-blue-300 rounded-lg px-2.5 py-1.5 bg-blue-50 hover:bg-blue-100 transition-colors font-medium"
          >
            <ShoppingCart size={11} />
            Draft PO
          </button>
        ) : !alert.needs_po ? (
          <span className="text-xs text-emerald-600 font-medium">PO exists</span>
        ) : null}
      </td>
    </tr>
  );
}

export function Inventory() {
  const [items,    setItems]    = useState<InventoryAlert[]>([]);
  const [loading,  setLoading]  = useState(true);
  const [search,   setSearch]   = useState('');
  const [filter,   setFilter]   = useState<FilterType>('all');
  const [drafting, setDrafting] = useState<string | null>(null);
  const { canDraftPO } = useAuth();

  useEffect(() => {
    api.inventory.list()
      .then((r) => setItems(r.items ?? []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, []);

  const draftPO = async (id: string, name: string) => {
    setDrafting(id);
    try {
      await api.post('/api/actions/draft-po', { item_id: id, product_name: name });
      toast.success(`PO draft created for ${name} — check approvals`);
    } catch {
      toast.error('Could not create PO draft');
    } finally {
      setDrafting(null);
    }
  };

  const filtered = items.filter((a) => {
    const matchFilter = filter === 'all' || a.stock_status === filter;
    const matchSearch = !search || a.product_name.toLowerCase().includes(search.toLowerCase());
    return matchFilter && matchSearch;
  });

  const counts = FILTERS.reduce((acc, f) => ({
    ...acc,
    [f]: f === 'all' ? items.length : items.filter((a) => a.stock_status === f).length,
  }), {} as Record<string, number>);

  const criticalCount = items.filter((a) => a.stock_status !== 'low').length;

  return (
    <div className="flex flex-col h-screen bg-slate-50">
      {/* Header */}
      <div className="bg-white border-b border-slate-100 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-slate-900">Inventory</h1>
            <p className="text-sm text-slate-400 mt-0.5">
              {items.length} alerts
              {criticalCount > 0 && <span className="text-orange-600 font-medium ml-1">· {criticalCount} critical</span>}
            </p>
          </div>
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search products…"
              className="pl-8 pr-4 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white w-56"
            />
          </div>
        </div>

        {/* Filter tabs */}
        <div className="flex gap-1 mt-4">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
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
          <div className="flex items-center justify-center h-40 text-slate-400 text-sm">
            <Loader2 size={16} className="animate-spin mr-2" /> Loading…
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 text-slate-400">
            <Package size={24} className="mb-2 opacity-40" />
            <p className="text-sm">
              {items.length === 0 ? 'All products at healthy stock levels' : 'No items match this filter'}
            </p>
          </div>
        ) : (
          <table className="w-full">
            <thead className="bg-white border-b border-slate-100 sticky top-0">
              <tr>
                {['Product', 'Status', 'Qty on Hand', 'Est. Runway', 'Action'].map((h) => (
                  <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {filtered.map((a) => (
                <InventoryRow
                  key={a.item_id}
                  alert={a}
                  onDraftPO={draftPO}
                  canDraftPO={canDraftPO}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
