import React, { useState } from 'react';
import clsx from 'clsx';
import { Truck, ClipboardList, FileText, Ship, BarChart3 } from 'lucide-react';

import { ReorderQueue, ReorderItem } from '../components/logistics/ReorderQueue';
import { PurchaseOrders, PurchaseOrder } from '../components/logistics/PurchaseOrders';
import { ShipmentTracker, Shipment } from '../components/logistics/ShipmentTracker';
import { CostAnalysis, CostVarianceRow } from '../components/logistics/CostAnalysis';

// ── Tab definitions ───────────────────────────────────────────────────────────

type TabId = 'reorder' | 'pos' | 'shipments' | 'costs';

const TABS: { id: TabId; label: string; icon: React.ElementType }[] = [
  { id: 'reorder',   label: 'Reorder Queue',    icon: ClipboardList },
  { id: 'pos',       label: 'Purchase Orders',  icon: FileText      },
  { id: 'shipments', label: 'Shipments',         icon: Ship          },
  { id: 'costs',     label: 'Cost Analysis',     icon: BarChart3     },
];

// ── Empty initial state (no mock data) ───────────────────────────────────────

const EMPTY_REORDER: ReorderItem[]       = [];
const EMPTY_POS: PurchaseOrder[]         = [];
const EMPTY_SHIPMENTS: Shipment[]        = [];
const EMPTY_COST_ROWS: CostVarianceRow[] = [];

// ── Page ─────────────────────────────────────────────────────────────────────

export function Logistics() {
  const [activeTab, setActiveTab] = useState<TabId>('reorder');

  // Reorder queue state
  const [reorderItems, setReorderItems] = useState<ReorderItem[]>(EMPTY_REORDER);

  // PO state
  const [purchaseOrders] = useState<PurchaseOrder[]>(EMPTY_POS);

  // Shipments state
  const [shipments] = useState<Shipment[]>(EMPTY_SHIPMENTS);

  // Cost analysis state
  const [costRows] = useState<CostVarianceRow[]>(EMPTY_COST_ROWS);

  // Count badges
  const reorderCount  = reorderItems.length;
  const poCount       = purchaseOrders.length;
  const shipmentCount = shipments.length;
  const delayedCount  = shipments.filter((s) => s.is_delayed).length;

  return (
    <div className="flex flex-col h-screen bg-slate-50">
      {/* Page header */}
      <div className="bg-white border-b border-slate-100 px-6 py-4 flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-blue-50 flex items-center justify-center">
            <Truck size={18} className="text-blue-600" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-slate-900">Logistics Manager</h1>
            <p className="text-sm text-slate-400 mt-0.5">
              Reordering · Purchase orders · Shipments · Cost analysis
            </p>
          </div>

          {/* Quick stats */}
          <div className="ml-auto flex items-center gap-4">
            {reorderCount > 0 && (
              <div className="text-center">
                <div className="text-lg font-bold text-slate-800">{reorderCount}</div>
                <div className="text-xs text-slate-400">Pending reorders</div>
              </div>
            )}
            {poCount > 0 && (
              <div className="text-center">
                <div className="text-lg font-bold text-slate-800">{poCount}</div>
                <div className="text-xs text-slate-400">Open POs</div>
              </div>
            )}
            {delayedCount > 0 && (
              <div className="text-center">
                <div className="text-lg font-bold text-red-600">{delayedCount}</div>
                <div className="text-xs text-slate-400">Delayed</div>
              </div>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mt-4">
          {TABS.map(({ id, label, icon: Icon }) => {
            const badge =
              id === 'reorder'   ? reorderCount  :
              id === 'pos'       ? poCount        :
              id === 'shipments' ? shipmentCount  :
              0;

            return (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className={clsx(
                  'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors',
                  activeTab === id
                    ? 'bg-slate-800 text-white'
                    : 'text-slate-500 hover:bg-slate-100 hover:text-slate-700',
                )}
              >
                <Icon size={14} />
                {label}
                {badge > 0 && (
                  <span className={clsx(
                    'px-1.5 py-0.5 rounded-full text-[10px] font-bold min-w-[18px] text-center leading-none',
                    activeTab === id
                      ? 'bg-white/20 text-white'
                      : 'bg-slate-200 text-slate-600',
                  )}>
                    {badge}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-auto">
        <div className="bg-white min-h-full shadow-sm">
          {activeTab === 'reorder' && (
            <ReorderQueue
              items={reorderItems}
              loading={false}
              onItemsChange={setReorderItems}
            />
          )}

          {activeTab === 'pos' && (
            <PurchaseOrders
              orders={purchaseOrders}
              loading={false}
            />
          )}

          {activeTab === 'shipments' && (
            <ShipmentTracker
              shipments={shipments}
              loading={false}
            />
          )}

          {activeTab === 'costs' && (
            <CostAnalysis
              rows={costRows}
              loading={false}
            />
          )}
        </div>
      </div>
    </div>
  );
}
