import React, { useState } from 'react';
import clsx from 'clsx';
import { Ship, AlertTriangle, Clock, RefreshCw, Anchor } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../lib/api';

export interface CostComponent {
  label: string;
  amount: number;
  currency: string;
}

export interface Shipment {
  id: string;
  booking_ref: string;
  vessel: string;
  origin_port: string;
  destination_port: string;
  eta: string;
  etd?: string;
  is_delayed: boolean;
  delay_days?: number;
  status: 'in_transit' | 'at_port' | 'customs' | 'delivered' | 'on_hold';
  cost_components: CostComponent[];
  supplier?: string;
  po_numbers?: string[];
}

interface ShipmentTrackerProps {
  shipments: Shipment[];
  loading: boolean;
}

const STATUS_STYLES: Record<Shipment['status'], { label: string; className: string }> = {
  in_transit: { label: 'In Transit',  className: 'bg-blue-100 text-blue-700'    },
  at_port:    { label: 'At Port',     className: 'bg-slate-100 text-slate-600'  },
  customs:    { label: 'Customs',     className: 'bg-amber-100 text-amber-700'  },
  delivered:  { label: 'Delivered',   className: 'bg-green-100 text-green-700'  },
  on_hold:    { label: 'On Hold',     className: 'bg-red-100 text-red-700'      },
};

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function ShipmentCard({ shipment }: { shipment: Shipment }) {
  const [checking, setChecking] = useState(false);
  const statusCfg = STATUS_STYLES[shipment.status];
  const totalCost = shipment.cost_components.reduce((s, c) => s + c.amount, 0);

  const handleCheckStatus = async () => {
    setChecking(true);
    try {
      await api.logistics.demurrageRisk({ booking_ref: shipment.booking_ref, shipment_id: shipment.id });
      toast.success(`Status checked for ${shipment.booking_ref}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Status check failed');
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className={clsx(
      'bg-white rounded-xl shadow-sm border p-5 flex flex-col gap-4',
      shipment.is_delayed ? 'border-red-200' : 'border-slate-200',
    )}>
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className={clsx(
            'w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0',
            shipment.is_delayed ? 'bg-red-50' : 'bg-blue-50',
          )}>
            <Ship size={16} className={shipment.is_delayed ? 'text-red-500' : 'text-blue-500'} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-slate-800 font-mono">{shipment.booking_ref}</span>
              <span className={clsx('px-2 py-0.5 rounded text-xs font-semibold', statusCfg.className)}>
                {statusCfg.label}
              </span>
            </div>
            <div className="text-xs text-slate-500 mt-0.5">{shipment.vessel}</div>
            {shipment.supplier && (
              <div className="text-xs text-slate-400 mt-0.5">Supplier: {shipment.supplier}</div>
            )}
          </div>
        </div>

        {shipment.is_delayed && (
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-red-50 border border-red-200 text-red-700 text-xs font-semibold flex-shrink-0">
            <AlertTriangle size={11} />
            Delayed {shipment.delay_days != null ? `+${shipment.delay_days}d` : ''}
          </div>
        )}
      </div>

      {/* Route & ETA */}
      <div className="grid grid-cols-3 gap-3 text-xs">
        <div>
          <div className="text-slate-400 font-medium uppercase tracking-wide mb-0.5">Origin</div>
          <div className="text-slate-700 font-medium">{shipment.origin_port}</div>
          {shipment.etd && <div className="text-slate-400">ETD {fmtDate(shipment.etd)}</div>}
        </div>
        <div className="flex flex-col items-center justify-center text-slate-300">
          <div className="w-full border-t border-dashed border-slate-200 relative">
            <Anchor size={10} className="absolute right-0 top-1/2 -translate-y-1/2 text-slate-300" />
          </div>
        </div>
        <div>
          <div className="text-slate-400 font-medium uppercase tracking-wide mb-0.5">Destination</div>
          <div className="text-slate-700 font-medium">{shipment.destination_port}</div>
          <div className={clsx('flex items-center gap-1', shipment.is_delayed ? 'text-red-600' : 'text-slate-400')}>
            <Clock size={10} />
            ETA {fmtDate(shipment.eta)}
          </div>
        </div>
      </div>

      {/* Cost components */}
      {shipment.cost_components.length > 0 && (
        <div className="border-t border-slate-100 pt-3">
          <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Cost Breakdown</div>
          <div className="space-y-1">
            {shipment.cost_components.map((c, i) => (
              <div key={i} className="flex justify-between text-xs">
                <span className="text-slate-500">{c.label}</span>
                <span className="text-slate-700 font-medium">
                  {c.currency} {c.amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </span>
              </div>
            ))}
            <div className="flex justify-between text-xs font-semibold border-t border-slate-100 pt-1.5 mt-1.5">
              <span className="text-slate-600">Total</span>
              <span className="text-slate-800">
                {shipment.cost_components[0]?.currency ?? 'USD'} {totalCost.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* PO references */}
      {shipment.po_numbers && shipment.po_numbers.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {shipment.po_numbers.map((po) => (
            <span key={po} className="px-2 py-0.5 rounded bg-slate-100 text-slate-600 text-xs font-mono">{po}</span>
          ))}
        </div>
      )}

      {/* Footer action */}
      <div className="border-t border-slate-100 pt-3">
        <button
          onClick={handleCheckStatus}
          disabled={checking}
          className="flex items-center gap-2 text-xs text-blue-600 hover:text-blue-700 font-medium transition-colors"
        >
          <RefreshCw size={12} className={checking ? 'animate-spin' : ''} />
          {checking ? 'Checking…' : 'Check Status'}
        </button>
      </div>
    </div>
  );
}

export function ShipmentTracker({ shipments, loading }: ShipmentTrackerProps) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 p-6">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-56 bg-slate-100 rounded-xl animate-pulse" />
        ))}
      </div>
    );
  }

  if (shipments.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-slate-400 gap-3">
        <div className="w-14 h-14 rounded-2xl bg-slate-100 border border-slate-200 flex items-center justify-center">
          <Ship className="w-6 h-6 text-slate-300" />
        </div>
        <div className="text-center">
          <p className="text-sm font-medium text-slate-500">No active shipments</p>
          <p className="text-xs mt-1 text-slate-400">Shipments will appear here once POs are confirmed and booked</p>
        </div>
      </div>
    );
  }

  const delayed = shipments.filter((s) => s.is_delayed);

  return (
    <div className="p-6">
      {delayed.length > 0 && (
        <div className="flex items-center gap-2 px-4 py-3 mb-5 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm">
          <AlertTriangle size={15} />
          <span className="font-medium">{delayed.length} shipment{delayed.length !== 1 ? 's' : ''} delayed</span>
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {shipments.map((s) => (
          <ShipmentCard key={s.id} shipment={s} />
        ))}
      </div>
    </div>
  );
}
