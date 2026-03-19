import React from 'react';
import clsx from 'clsx';
import { AlertTriangle, Package, XCircle } from 'lucide-react';

interface InventoryAlert {
  item_id: string;
  product_name: string;
  total_qty: number;
  weeks_remaining: number | null;
  stock_status: 'low' | 'critical' | 'out_of_stock';
  needs_po: boolean;
}

interface Props {
  alerts: InventoryAlert[];
}

const STATUS_CONFIG = {
  critical: { label: 'Critical', className: 'bg-orange-50 border-orange-200', icon: AlertTriangle, iconClass: 'text-orange-500' },
  out_of_stock: { label: 'Out of Stock', className: 'bg-red-50 border-red-200', icon: XCircle, iconClass: 'text-red-500' },
  low: { label: 'Low', className: 'bg-yellow-50 border-yellow-200', icon: Package, iconClass: 'text-yellow-500' },
} as const;

export function InventoryAlerts({ alerts }: Props) {
  if (alerts.length === 0) {
    return (
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
          Inventory Alerts
        </h2>
        <p className="text-sm text-green-600 font-medium">All products at healthy levels ✓</p>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-5">
      <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Inventory Alerts ({alerts.length})
      </h2>
      <div className="space-y-2">
        {alerts.map((alert) => {
          const config = STATUS_CONFIG[alert.stock_status];
          const Icon = config.icon;
          return (
            <div
              key={alert.item_id}
              className={clsx('flex items-start gap-3 rounded-lg border px-3 py-2.5', config.className)}
            >
              <Icon size={16} className={clsx('mt-0.5 flex-shrink-0', config.iconClass)} />
              <div className="flex-1 min-w-0">
                <div className="font-medium text-gray-800 text-sm truncate">{alert.product_name}</div>
                <div className="text-xs text-gray-500">
                  {alert.total_qty} cases
                  {alert.weeks_remaining != null && ` · ~${alert.weeks_remaining.toFixed(1)} wks`}
                  {alert.needs_po && (
                    <span className="ml-1 font-medium text-orange-600">· No PO in system</span>
                  )}
                </div>
              </div>
              <span
                className={clsx(
                  'flex-shrink-0 text-xs font-medium px-2 py-0.5 rounded-full',
                  config.iconClass,
                  config.className,
                )}
              >
                {config.label}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
