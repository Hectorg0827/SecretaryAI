import { useCallback, useEffect, useState } from 'react';
import clsx from 'clsx';
import { AlertTriangle, CalendarClock, Info, Loader2, TrendingUp, X } from 'lucide-react';
import { api } from '../../lib/api';

/** Response of GET /api/inventory/{item_id}/forecast (backend/app/api/inventory.py). */
export interface ItemForecast {
  product_name: string;
  current_stock: number;
  projected_stockout_date: string | null;
  recommended_reorder_date: string | null;
  recommended_order_qty: number;
  weekly_sell_history: number[];
  historical_invoice_count: number;
  lead_time_weeks: number;
  lead_time_source: 'product' | 'industry_default';
  data_source: string;
  confidence: 'high' | 'medium' | 'low';
}

const CONFIDENCE_CFG = {
  high:   { label: 'High confidence',   badge: 'bg-emerald-100 text-emerald-700' },
  medium: { label: 'Medium confidence', badge: 'bg-amber-100 text-amber-700' },
  low:    { label: 'Low confidence',    badge: 'bg-orange-100 text-orange-700' },
} as const;

/** Parse a backend calendar date ("YYYY-MM-DD") as a local date. */
function parseDay(value: string | null): Date | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function formatDay(day: Date): string {
  return day.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function startOfToday(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function formatUnits(n: number): string {
  return `${Math.round(n).toLocaleString('en-US')} units`;
}

/** Average of the last four weeks, the same window the backend forecast uses. */
export function recentWeeklyAverage(history: number[]): number {
  const recent = history.slice(-4);
  return recent.length ? recent.reduce((sum, qty) => sum + qty, 0) / recent.length : 0;
}

function Stat({ label, value, hint, tone = 'default' }: {
  label: string; value: string; hint?: string; tone?: 'default' | 'warn';
}) {
  return (
    <div className={clsx('rounded-xl border px-4 py-3', tone === 'warn' ? 'border-orange-200 bg-orange-50' : 'border-slate-200 bg-white')}>
      <div className="text-xs font-medium text-slate-500">{label}</div>
      <div className={clsx('text-base font-semibold mt-0.5', tone === 'warn' ? 'text-orange-700' : 'text-slate-900')}>{value}</div>
      {hint && <div className="text-xs text-slate-500 mt-0.5">{hint}</div>}
    </div>
  );
}

function SalesHistoryBars({ history }: { history: number[] }) {
  const max = Math.max(...history, 0);
  return (
    <div>
      <div className="text-xs font-medium text-slate-500 mb-2">Units sold per week, last 12 weeks (oldest on the left)</div>
      <div className="flex items-end gap-1 h-20" aria-hidden="true">
        {history.map((qty, i) => (
          <div
            key={i}
            title={`${Math.round(qty)} units`}
            className={clsx('flex-1 rounded-t', qty > 0 ? 'bg-blue-400' : 'bg-slate-200')}
            style={{ height: `${max > 0 ? Math.max(4, (qty / max) * 100) : 4}%` }}
          />
        ))}
      </div>
    </div>
  );
}

function ForecastBody({ forecast }: { forecast: ItemForecast }) {
  const history = forecast.weekly_sell_history ?? [];
  const leadTime = forecast.lead_time_source === 'product'
    ? `${forecast.lead_time_weeks} weeks (from this product's settings)`
    : `${forecast.lead_time_weeks} weeks (typical default — no lead time is set for this product)`;

  if (history.length === 0) {
    return (
      <div className="space-y-4">
        <div role="status" className="flex gap-2 items-start rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-800">
          <Info size={16} className="mt-0.5 flex-shrink-0" />
          <div>
            <div className="font-semibold">Not enough sales history to forecast</div>
            <div className="mt-0.5">
              We found no QuickBooks invoices for this product in the last 12 weeks, so no
              run-out date, reorder date or order quantity is suggested.
            </div>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Stat label="In stock now" value={formatUnits(forecast.current_stock)} />
          <Stat label="Lead time" value={`${forecast.lead_time_weeks} weeks`} hint={leadTime} />
        </div>
      </div>
    );
  }

  const today = startOfToday();
  const stockout = parseDay(forecast.projected_stockout_date);
  const reorder = parseDay(forecast.recommended_reorder_date);
  const reorderOverdue = reorder !== null && reorder <= today;
  const soldTotal = history.reduce((sum, qty) => sum + qty, 0);
  const weeksWithSales = history.filter((qty) => qty > 0).length;
  const confidence = CONFIDENCE_CFG[forecast.confidence] ?? CONFIDENCE_CFG.low;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span className={clsx('font-semibold px-2 py-1 rounded-lg', confidence.badge)}>{confidence.label}</span>
        <span>
          Based on {forecast.historical_invoice_count} QuickBooks invoice{forecast.historical_invoice_count === 1 ? '' : 's'}
          {' '}with sales in {weeksWithSales} of the last 12 weeks.
        </span>
      </div>

      {forecast.confidence === 'low' && (
        <div role="note" className="flex gap-2 items-start rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <AlertTriangle size={14} className="mt-0.5 flex-shrink-0" />
          <span>Limited sales history (fewer than 4 weeks with sales). Treat these numbers as a rough estimate.</span>
        </div>
      )}

      {reorder ? (
        <div className={clsx(
          'flex gap-3 items-start rounded-xl border px-4 py-3',
          reorderOverdue ? 'border-red-200 bg-red-50' : 'border-blue-200 bg-blue-50',
        )}>
          <CalendarClock size={18} className={clsx('mt-0.5 flex-shrink-0', reorderOverdue ? 'text-red-600' : 'text-blue-600')} />
          <div>
            <div className="text-xs font-medium text-slate-600">Recommended reorder date</div>
            <div className={clsx('text-lg font-bold', reorderOverdue ? 'text-red-700' : 'text-slate-900')}>
              {reorderOverdue ? 'Reorder now' : formatDay(reorder)}
            </div>
            {reorderOverdue && (
              <div className="text-xs text-red-700">The recommended date ({formatDay(reorder)}) has already passed.</div>
            )}
          </div>
        </div>
      ) : (
        <div role="status" className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
          No reorder date suggested: there were no sales of this product in the last 4 weeks, so we
          can&apos;t project when it will run out.
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <Stat label="In stock now" value={formatUnits(forecast.current_stock)} />
        <Stat
          label="Projected to run out"
          value={stockout ? formatDay(stockout) : 'Not projected'}
          tone={stockout && stockout <= today ? 'warn' : 'default'}
        />
        <Stat
          label="Suggested order quantity"
          value={forecast.recommended_order_qty > 0 ? formatUnits(forecast.recommended_order_qty) : '—'}
          hint={forecast.recommended_order_qty > 0 ? 'About 8 weeks of stock at the recent sales rate' : undefined}
        />
        <Stat
          label="Average weekly sales"
          value={formatUnits(recentWeeklyAverage(history))}
          hint={`Last 4 weeks · ${formatUnits(soldTotal)} sold in 12 weeks`}
        />
      </div>

      <div className="text-xs text-slate-500">Lead time used: {leadTime}</div>

      <SalesHistoryBars history={history} />
    </div>
  );
}

export function ForecastPanel({ itemId, productName, onClose }: {
  itemId: string; productName: string; onClose: () => void;
}) {
  const [forecast, setForecast] = useState<ItemForecast | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api.get<ItemForecast>(`/api/inventory/${encodeURIComponent(itemId)}/forecast`)
      .then((result) => setForecast(result))
      .catch((err: unknown) => {
        setForecast(null);
        setError(err instanceof Error ? err.message : 'The forecast is unavailable right now.');
      })
      .finally(() => setLoading(false));
  }, [itemId]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Demand forecast for ${productName}`}
        className="w-full max-w-xl max-h-[90vh] overflow-auto rounded-xl border border-slate-200 bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div className="flex items-start gap-2">
            <TrendingUp size={18} className="mt-0.5 text-blue-600" />
            <div>
              <h2 className="text-base font-bold text-slate-900">Demand forecast</h2>
              <p className="text-sm text-slate-500">{forecast?.product_name || productName}</p>
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Close forecast" className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-slate-500" role="status">
              <Loader2 size={16} className="animate-spin" /> Loading forecast…
            </div>
          ) : error ? (
            <div role="alert" className="flex gap-2 items-start rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
              <div className="space-y-1">
                <div>No forecast was made: {error}</div>
                <div className="text-xs">
                  This usually means QuickBooks sales history couldn&apos;t be read right now. Nothing has been estimated.
                </div>
                <button type="button" onClick={load} className="text-xs font-semibold underline">Try again</button>
              </div>
            </div>
          ) : forecast ? (
            <ForecastBody forecast={forecast} />
          ) : null}

          <p className="text-xs text-slate-400">
            Forecast only, from QuickBooks invoices in the last 12 weeks. Nothing is ordered or changed.
          </p>
        </div>
      </div>
    </div>
  );
}
