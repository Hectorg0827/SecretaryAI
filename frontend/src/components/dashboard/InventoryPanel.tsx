import React, { useState } from 'react';
import clsx from 'clsx';
import { AlertTriangle, XCircle, Package, ChevronRight, ShoppingCart, Loader2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { api, InventoryAlert } from '../../lib/api';
import { Card, CardHeader } from '../ui/Card';

const STATUS_CFG = {
  out_of_stock: { label: 'Out of Stock', icon: XCircle,       border: 'border-l-red-500',    bg: 'bg-red-50',     text: 'text-red-700' },
  critical:     { label: 'Critical',     icon: AlertTriangle, border: 'border-l-orange-400', bg: 'bg-orange-50',  text: 'text-orange-700' },
  low:          { label: 'Low',          icon: Package,       border: 'border-l-amber-400',  bg: 'bg-amber-50',   text: 'text-amber-700' },
} as const;

function AlertRow({ alert, onDraftPO }: { alert: InventoryAlert; onDraftPO: (id: string, name: string) => void }) {
  const cfg = STATUS_CFG[alert.stock_status];
  const Icon = cfg.icon;
  return (
    <div className={clsx('flex items-center gap-3 px-4 py-3 border-l-4', cfg.border)}>
      <Icon size={14} className={clsx('flex-shrink-0', cfg.text)} />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-slate-800 truncate">{alert.product_name}</div>
        <div className="text-xs text-slate-400">
          {alert.total_qty} units
          {alert.weeks_remaining != null && ` · ~${alert.weeks_remaining.toFixed(1)} wks`}
          {alert.needs_po && <span className="ml-1 text-orange-600 font-medium">· No PO</span>}
        </div>
      </div>
      <span className={clsx('text-xs font-semibold px-2 py-0.5 rounded', cfg.bg, cfg.text)}>
        {cfg.label}
      </span>
      {alert.needs_po && (
        <button
          onClick={() => onDraftPO(alert.item_id, alert.product_name)}
          className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700 border border-blue-200 hover:border-blue-300 rounded-lg px-2 py-1 bg-blue-50 hover:bg-blue-100 transition-colors whitespace-nowrap"
        >
          <ShoppingCart size={11} />
          Draft PO
        </button>
      )}
    </div>
  );
}

export function InventoryPanel({ alerts }: { alerts: InventoryAlert[] }) {
  const navigate = useNavigate();
  const [drafting, setDrafting] = useState<string | null>(null);

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

  const critical = alerts.filter((a) => a.stock_status !== 'low').length;

  return (
    <Card>
      <CardHeader
        title="Inventory Alerts"
        count={alerts.length > 0 ? (critical > 0 ? `${critical} critical` : `${alerts.length} alerts`) : undefined}
        action={
          <button
            onClick={() => navigate('/inventory')}
            className="flex items-center gap-0.5 text-xs text-blue-600 hover:text-blue-700 font-medium"
          >
            View all <ChevronRight size={12} />
          </button>
        }
      />

      {alerts.length === 0 ? (
        <div className="flex items-center gap-2 px-5 py-4 text-sm text-emerald-600 font-medium">
          <Package size={16} className="text-emerald-500" />
          All products at healthy levels
        </div>
      ) : (
        <div className="divide-y divide-slate-50">
          {alerts.slice(0, 6).map((a) => (
            <AlertRow
              key={a.item_id}
              alert={a}
              onDraftPO={(id, name) => draftPO(id, name)}
            />
          ))}
          {alerts.length > 6 && (
            <button
              onClick={() => navigate('/inventory')}
              className="w-full text-xs text-slate-400 hover:text-blue-600 text-center py-2.5 transition-colors"
            >
              +{alerts.length - 6} more alerts →
            </button>
          )}
        </div>
      )}
    </Card>
  );
}
