import React, { useState } from 'react';
import clsx from 'clsx';
import { ChevronDown, ChevronRight, FileText, Mail } from 'lucide-react';

export interface POLineItem {
  sku_id: string;
  sku_name: string;
  qty: number;
  unit_cost: number;
  total: number;
}

export type POStatus = 'draft' | 'sent' | 'confirmed' | 'partial' | 'received' | 'cancelled';

export interface PurchaseOrder {
  id: string;
  po_number: string;
  supplier: string;
  cases: number;
  total_value: number;
  status: POStatus;
  created_at: string;
  line_items: POLineItem[];
  email_body?: string;
}

interface PurchaseOrdersProps {
  orders: PurchaseOrder[];
  loading: boolean;
}

const STATUS_STYLES: Record<POStatus, string> = {
  draft:      'bg-slate-100 text-slate-600',
  sent:       'bg-blue-100 text-blue-700',
  confirmed:  'bg-emerald-100 text-emerald-700',
  partial:    'bg-amber-100 text-amber-700',
  received:   'bg-green-100 text-green-700',
  cancelled:  'bg-red-100 text-red-700',
};

const STATUS_LABELS: Record<POStatus, string> = {
  draft:      'Draft',
  sent:       'Sent',
  confirmed:  'Confirmed',
  partial:    'Partial',
  received:   'Received',
  cancelled:  'Cancelled',
};

function PORow({ order }: { order: PurchaseOrder }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <>
      <tr
        className="hover:bg-slate-50 transition-colors cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
      >
        <td className="px-5 py-3.5">
          <div className="flex items-center gap-2">
            {expanded ? <ChevronDown size={14} className="text-slate-400" /> : <ChevronRight size={14} className="text-slate-400" />}
            <span className="text-sm font-mono font-semibold text-slate-800">{order.po_number}</span>
          </div>
        </td>
        <td className="px-5 py-3.5 text-sm text-slate-700">{order.supplier}</td>
        <td className="px-5 py-3.5 text-sm text-slate-700">{order.cases.toLocaleString()}</td>
        <td className="px-5 py-3.5 text-sm font-semibold text-slate-800">
          ${order.total_value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </td>
        <td className="px-5 py-3.5">
          <span className={clsx('px-2 py-0.5 rounded text-xs font-semibold', STATUS_STYLES[order.status])}>
            {STATUS_LABELS[order.status]}
          </span>
        </td>
        <td className="px-5 py-3.5 text-xs text-slate-400">
          {new Date(order.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
        </td>
      </tr>

      {expanded && (
        <tr>
          <td colSpan={6} className="bg-slate-50 px-6 py-4 border-b border-slate-100">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {/* Line items */}
              <div>
                <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                  <FileText size={12} />
                  Line Items
                </h4>
                {order.line_items.length === 0 ? (
                  <p className="text-xs text-slate-400 italic">No line items</p>
                ) : (
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-slate-400">
                        <th className="text-left py-1 font-medium">SKU</th>
                        <th className="text-right py-1 font-medium">Qty</th>
                        <th className="text-right py-1 font-medium">Unit Cost</th>
                        <th className="text-right py-1 font-medium">Total</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {order.line_items.map((li) => (
                        <tr key={li.sku_id} className="text-slate-700">
                          <td className="py-1.5">
                            <div className="font-medium">{li.sku_name}</div>
                            <div className="text-slate-400 font-mono">{li.sku_id}</div>
                          </td>
                          <td className="text-right py-1.5">{li.qty.toLocaleString()}</td>
                          <td className="text-right py-1.5">${li.unit_cost.toFixed(2)}</td>
                          <td className="text-right py-1.5 font-semibold">${li.total.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              {/* Email body preview */}
              {order.email_body && (
                <div>
                  <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                    <Mail size={12} />
                    Email Preview
                  </h4>
                  <pre className="text-xs text-slate-600 bg-white border border-slate-200 rounded-lg p-3 whitespace-pre-wrap font-sans max-h-48 overflow-y-auto">
                    {order.email_body}
                  </pre>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export function PurchaseOrders({ orders, loading }: PurchaseOrdersProps) {
  if (loading) {
    return (
      <div className="space-y-2 p-6">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-14 bg-slate-100 rounded-lg animate-pulse" />
        ))}
      </div>
    );
  }

  if (orders.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-slate-400 gap-3">
        <div className="w-14 h-14 rounded-2xl bg-slate-100 border border-slate-200 flex items-center justify-center">
          <FileText className="w-6 h-6 text-slate-300" />
        </div>
        <div className="text-center">
          <p className="text-sm font-medium text-slate-500">No purchase orders</p>
          <p className="text-xs mt-1 text-slate-400">Approve items in the Reorder Queue and generate POs to see them here</p>
        </div>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead className="bg-white border-b border-slate-100">
          <tr>
            {['PO Number', 'Supplier', 'Cases', 'Total Value', 'Status', 'Created'].map((h) => (
              <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {orders.map((order) => (
            <PORow key={order.id} order={order} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
