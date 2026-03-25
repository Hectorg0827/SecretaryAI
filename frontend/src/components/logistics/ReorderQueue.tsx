import React, { useState } from 'react';
import clsx from 'clsx';
import { ShoppingCart, ClipboardList, Check } from 'lucide-react';
import toast from 'react-hot-toast';
import { UrgencyBadge, Urgency } from './UrgencyBadge';
import { api } from '../../lib/api';

export interface ReorderItem {
  id: string;
  sku_name: string;
  sku_id: string;
  urgency: Urgency;
  days_of_supply: number;
  recommended_qty: number;
  container_fill_pct: number;
  lead_time_p80_days: number;
  unit_cost?: number;
  approved?: boolean;
}

interface ReorderQueueProps {
  items: ReorderItem[];
  loading: boolean;
  onItemsChange: (items: ReorderItem[]) => void;
}

export function ReorderQueue({ items, loading, onItemsChange }: ReorderQueueProps) {
  const [generatingPos, setGeneratingPos] = useState(false);

  const approvedItems = items.filter((i) => i.approved);

  const handleApprove = (id: string) => {
    onItemsChange(items.map((i) => i.id === id ? { ...i, approved: !i.approved } : i));
  };

  const handleGeneratePos = async () => {
    if (approvedItems.length === 0) {
      toast.error('No approved items to generate POs for');
      return;
    }
    setGeneratingPos(true);
    try {
      await api.logistics.generatePos(
        approvedItems.map((i) => ({
          sku_id: i.sku_id,
          sku_name: i.sku_name,
          recommended_qty: i.recommended_qty,
        })),
        {},
      );
      toast.success(`Generated POs for ${approvedItems.length} item(s)`);
      onItemsChange(items.map((i) => i.approved ? { ...i, approved: false } : i));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to generate POs');
    } finally {
      setGeneratingPos(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-2 p-6">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-14 bg-slate-100 rounded-lg animate-pulse" />
        ))}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-slate-400 gap-3">
        <div className="w-14 h-14 rounded-2xl bg-slate-100 border border-slate-200 flex items-center justify-center">
          <ClipboardList className="w-6 h-6 text-slate-300" />
        </div>
        <div className="text-center">
          <p className="text-sm font-medium text-slate-500">No reorder decisions</p>
          <p className="text-xs mt-1 text-slate-400">Run the reorder evaluation to populate this queue</p>
        </div>
      </div>
    );
  }

  return (
    <div>
      {/* Toolbar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-slate-100 bg-slate-50">
        <p className="text-sm text-slate-500">
          {items.length} item{items.length !== 1 ? 's' : ''} in queue
          {approvedItems.length > 0 && (
            <span className="ml-2 text-blue-600 font-medium">
              · {approvedItems.length} approved
            </span>
          )}
        </p>
        <button
          onClick={handleGeneratePos}
          disabled={generatingPos || approvedItems.length === 0}
          className={clsx(
            'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors',
            approvedItems.length > 0
              ? 'bg-blue-600 text-white hover:bg-blue-700'
              : 'bg-slate-100 text-slate-400 cursor-not-allowed',
          )}
        >
          <ShoppingCart size={14} />
          {generatingPos ? 'Generating…' : `Generate POs for approved (${approvedItems.length})`}
        </button>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-white border-b border-slate-100">
            <tr>
              {['SKU', 'Urgency', 'Days of Supply', 'Rec. Qty', 'Container Fill', 'Lead Time (p80)', 'Action'].map((h) => (
                <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {items.map((item) => (
              <tr
                key={item.id}
                className={clsx(
                  'hover:bg-slate-50 transition-colors border-l-4',
                  item.urgency === 'RED'    ? 'border-l-red-500'    : '',
                  item.urgency === 'YELLOW' ? 'border-l-amber-400'  : '',
                  item.urgency === 'GREEN'  ? 'border-l-green-400'  : '',
                  item.approved && 'bg-blue-50/40',
                )}
              >
                <td className="px-5 py-3.5">
                  <div className="text-sm font-semibold text-slate-800">{item.sku_name}</div>
                  <div className="text-xs text-slate-400 font-mono">{item.sku_id}</div>
                </td>
                <td className="px-5 py-3.5">
                  <UrgencyBadge level={item.urgency} />
                </td>
                <td className="px-5 py-3.5 text-sm text-slate-700">
                  {item.days_of_supply} days
                </td>
                <td className="px-5 py-3.5 text-sm text-slate-700">
                  {item.recommended_qty.toLocaleString()} units
                </td>
                <td className="px-5 py-3.5">
                  <div className="flex items-center gap-2">
                    <div className="w-20 h-2 bg-slate-100 rounded-full overflow-hidden">
                      <div
                        className={clsx(
                          'h-full rounded-full',
                          item.container_fill_pct >= 90 ? 'bg-green-500' :
                          item.container_fill_pct >= 70 ? 'bg-amber-400' : 'bg-red-400',
                        )}
                        style={{ width: `${Math.min(item.container_fill_pct, 100)}%` }}
                      />
                    </div>
                    <span className="text-xs text-slate-600">{item.container_fill_pct.toFixed(0)}%</span>
                  </div>
                </td>
                <td className="px-5 py-3.5 text-sm text-slate-700">
                  {item.lead_time_p80_days} days
                </td>
                <td className="px-5 py-3.5">
                  <button
                    onClick={() => handleApprove(item.id)}
                    className={clsx(
                      'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                      item.approved
                        ? 'bg-emerald-600 text-white hover:bg-emerald-700'
                        : 'bg-slate-100 text-slate-600 hover:bg-blue-50 hover:text-blue-700 border border-slate-200',
                    )}
                  >
                    {item.approved && <Check size={11} />}
                    {item.approved ? 'Approved' : 'Approve'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
