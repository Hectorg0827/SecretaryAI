import React, { useEffect, useState } from 'react';
import clsx from 'clsx';
import { Package, AlertTriangle, XCircle, ShoppingCart, Search, Loader2, CheckCircle2, TrendingUp } from 'lucide-react';
import toast from 'react-hot-toast';
import { api, InventoryAlert } from '../lib/api';
import { useAuth } from '../hooks/useAuth';
import { ForecastPanel } from '../components/inventory/ForecastPanel';

const STATUS_CFG = {
  out_of_stock: { label: 'Out of Stock', icon: XCircle,       border: 'border-l-red-500',    bg: 'bg-red-50',     text: 'text-red-700',    badge: 'bg-red-100 text-red-700'      },
  critical:     { label: 'Critical',     icon: AlertTriangle, border: 'border-l-orange-400', bg: 'bg-orange-50',  text: 'text-orange-700', badge: 'bg-orange-100 text-orange-700'  },
  low:          { label: 'Low',          icon: Package,       border: 'border-l-amber-400',  bg: 'bg-amber-50',   text: 'text-amber-700',  badge: 'bg-amber-100 text-amber-700'    },
  healthy:      { label: 'In Stock',     icon: CheckCircle2,  border: 'border-l-emerald-400', bg: 'bg-emerald-50', text: 'text-emerald-700', badge: 'bg-emerald-100 text-emerald-700' },
} as const;

// GET /api/inventory lists every product, including healthy stock.
type InventoryItem = Omit<InventoryAlert, 'stock_status'> & {
  stock_status: InventoryAlert['stock_status'] | 'healthy';
};

const FILTERS = ['all', 'out_of_stock', 'critical', 'low', 'healthy'] as const;
type FilterType = typeof FILTERS[number];

// GET /api/inventory returns one page at a time; 200 is the server's maximum.
export const INVENTORY_PAGE_SIZE = 200;
const INVENTORY_CHANGED = 'Inventory changed while it was loading. Please try again.';

type InventoryPage = { items: InventoryItem[]; total: number; offset?: number; limit?: number };

/**
 * Read every page of GET /api/inventory. Any failed or inconsistent page fails
 * the whole read, so the page never presents part of the list as all of it.
 */
export async function loadAllInventory(
  onProgress?: (loaded: number, total: number) => void,
): Promise<InventoryItem[]> {
  const all: InventoryItem[] = [];
  const seen = new Set<string>();
  let expectedTotal: number | null = null;
  for (;;) {
    const page = await api.get<InventoryPage>(
      `/api/inventory?limit=${INVENTORY_PAGE_SIZE}&offset=${all.length}`,
    );
    if (!page || !Array.isArray(page.items) || !Number.isSafeInteger(page.total) || page.total < 0) {
      throw new Error('The inventory response was incomplete.');
    }
    if (expectedTotal === null) expectedTotal = page.total;
    // A changed total or a repeated product means the list moved between pages.
    if (page.total !== expectedTotal) throw new Error(INVENTORY_CHANGED);
    for (const item of page.items) {
      if (item.item_id) {
        if (seen.has(item.item_id)) throw new Error(INVENTORY_CHANGED);
        seen.add(item.item_id);
      }
      all.push(item);
    }
    if (all.length > expectedTotal) throw new Error(INVENTORY_CHANGED);
    if (all.length === expectedTotal) return all;
    if (page.items.length === 0) throw new Error(INVENTORY_CHANGED);
    onProgress?.(all.length, expectedTotal);
  }
}

function InventoryRow({ alert, onDraftPO, onForecast, canDraftPO, drafting }: {
  alert: InventoryItem;
  onDraftPO: (id: string, name: string) => void;
  onForecast: (id: string, name: string) => void;
  canDraftPO: boolean;
  drafting: boolean;
}) {
  const cfg  = STATUS_CFG[alert.stock_status] ?? STATUS_CFG.low;
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
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onForecast(alert.item_id, alert.product_name)}
            aria-label={`View demand forecast for ${alert.product_name}`}
            className="flex items-center gap-1.5 text-xs text-slate-600 hover:text-slate-800 border border-slate-200 hover:border-slate-300 rounded-lg px-2.5 py-1.5 bg-white hover:bg-slate-50 transition-colors font-medium"
          >
            <TrendingUp size={11} />
            Forecast
          </button>
          {alert.needs_po && canDraftPO && (
            <button
              type="button"
              onClick={() => onDraftPO(alert.item_id, alert.product_name)}
              disabled={drafting}
              className="flex items-center gap-1.5 text-xs text-blue-600 hover:text-blue-700 border border-blue-200 hover:border-blue-300 rounded-lg px-2.5 py-1.5 bg-blue-50 hover:bg-blue-100 transition-colors font-medium disabled:opacity-50"
            >
              <ShoppingCart size={11} />
              Draft PO
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

export function Inventory() {
  const [items,    setItems]    = useState<InventoryItem[]>([]);
  const [loading,  setLoading]  = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ loaded: number; total: number } | null>(null);
  const [reload, setReload] = useState(0);
  const [forecastItem, setForecastItem] = useState<{ id: string; name: string } | null>(null);
  const [search,   setSearch]   = useState('');
  const [filter,   setFilter]   = useState<FilterType>('all');
  const [drafting, setDrafting] = useState<string | null>(null);
  const { canDraftPO } = useAuth();

  useEffect(() => {
    let current = true;
    setLoading(true);
    setLoadError(null);
    setProgress(null);
    loadAllInventory((loaded, total) => { if (current) setProgress({ loaded, total }); })
      .then((all) => { if (current) setItems(all); })
      .catch((err: unknown) => {
        if (!current) return;
        // A failed read is an outage, never an empty "all healthy" list, and
        // a partly loaded list is never shown as the whole inventory.
        setItems([]);
        setLoadError(err instanceof Error && err.message ? err.message : 'Inventory is unavailable right now.');
      })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [reload]);

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

  const criticalCount = items.filter((a) => a.stock_status === 'critical' || a.stock_status === 'out_of_stock').length;

  return (
    <div className="flex flex-col h-full bg-slate-50">
      {/* Header */}
      <div className="bg-white border-b border-slate-100 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-slate-900">Inventory</h1>
            <p className="text-sm text-slate-400 mt-0.5">
              {loading || loadError ? 'Product count not available yet' : `${items.length} products`}
              {!loading && !loadError && criticalCount > 0 && (
                <span className="text-orange-600 font-medium ml-1">· {criticalCount} critical</span>
              )}
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
              {f === 'all' ? 'All' : STATUS_CFG[f].label}{!loading && !loadError && ` (${counts[f] ?? 0})`}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {loading ? (
          <div role="status" className="flex items-center justify-center h-40 text-slate-400 text-sm">
            <Loader2 size={16} className="animate-spin mr-2" />
            {progress
              ? `Loading products… ${progress.loaded.toLocaleString()} of ${progress.total.toLocaleString()}`
              : 'Loading…'}
          </div>
        ) : loadError ? (
          <div className="p-6">
            <div role="alert" className="flex gap-2 items-start rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
              <div className="space-y-1">
                <p>Inventory could not be loaded: {loadError}</p>
                <button
                  type="button"
                  onClick={() => setReload((n) => n + 1)}
                  className="font-medium underline hover:text-red-800"
                >
                  Try again
                </button>
              </div>
            </div>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 text-slate-400">
            <Package size={24} className="mb-2 opacity-40" />
            <p className="text-sm">
              {items.length === 0 ? 'No inventory items found' : 'No items match this filter'}
            </p>
          </div>
        ) : (
          <table className="w-full">
            <thead className="bg-white border-b border-slate-100 sticky top-0">
              <tr>
                {['Product', 'Status', 'Qty on Hand', 'Est. Runway', 'Actions'].map((h) => (
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
                  onForecast={(id, name) => setForecastItem({ id, name })}
                  canDraftPO={canDraftPO}
                  drafting={drafting === a.item_id}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {forecastItem && (
        <ForecastPanel
          itemId={forecastItem.id}
          productName={forecastItem.name}
          onClose={() => setForecastItem(null)}
        />
      )}
    </div>
  );
}
