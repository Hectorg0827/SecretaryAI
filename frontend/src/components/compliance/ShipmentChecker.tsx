import React, { useState } from 'react';
import clsx from 'clsx';
import { Package, CheckCircle2, XCircle, AlertTriangle, Info, Loader2 } from 'lucide-react';
import { api, ComplianceCheckResult, ComplianceIssue } from '../../lib/api';

interface ShipmentCheckerProps {
  loading: boolean;
}

function SeverityIcon({ severity }: { severity: string }) {
  const s = severity.toLowerCase();
  if (s === 'blocker' || s === 'critical' || s === 'error') {
    return <span className="text-base leading-none">🔴</span>;
  }
  if (s === 'warning' || s === 'warn') {
    return <span className="text-base leading-none">🟡</span>;
  }
  return <span className="text-base leading-none">ℹ️</span>;
}

export function ShipmentChecker({ loading: _parentLoading }: ShipmentCheckerProps) {
  const [productId, setProductId] = useState('');
  const [stateCode, setStateCode] = useState('');
  const [quantity, setQuantity] = useState('');
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<ComplianceCheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleCheck = async () => {
    if (!productId.trim() || !stateCode.trim() || !quantity) return;
    setChecking(true);
    setResult(null);
    setError(null);
    try {
      const res = await api.compliance.checkShipment({
        product_id: productId.trim(),
        state_code: stateCode.trim().toUpperCase(),
        quantity_cases: Number(quantity),
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to check compliance');
    } finally {
      setChecking(false);
    }
  };

  const isValid = productId.trim() && stateCode.trim().length === 2 && Number(quantity) > 0;

  const feeEntries = result ? Object.entries(result.fees).filter(([, v]) => v > 0) : [];

  return (
    <div className="p-6 max-w-2xl">
      {/* Form */}
      <div className="bg-white border border-slate-200 rounded-xl p-6 space-y-4">
        <div className="flex items-center gap-2 mb-2">
          <Package size={16} className="text-slate-500" />
          <h2 className="text-sm font-semibold text-slate-700">Shipment Compliance Check</h2>
        </div>

        <div className="grid grid-cols-3 gap-4">
          <div className="col-span-1">
            <label className="block text-xs font-medium text-slate-500 mb-1">Product ID</label>
            <input
              type="text"
              value={productId}
              onChange={(e) => setProductId(e.target.value)}
              placeholder="e.g. SKU-001"
              className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <div className="col-span-1">
            <label className="block text-xs font-medium text-slate-500 mb-1">State Code</label>
            <input
              type="text"
              value={stateCode}
              maxLength={2}
              onChange={(e) => setStateCode(e.target.value.toUpperCase())}
              placeholder="e.g. NY"
              className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent uppercase"
            />
          </div>
          <div className="col-span-1">
            <label className="block text-xs font-medium text-slate-500 mb-1">Quantity (cases)</label>
            <input
              type="number"
              value={quantity}
              min={1}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="e.g. 100"
              className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
        </div>

        <button
          onClick={handleCheck}
          disabled={!isValid || checking}
          className={clsx(
            'flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium transition-colors',
            isValid && !checking
              ? 'bg-blue-600 text-white hover:bg-blue-700'
              : 'bg-slate-100 text-slate-400 cursor-not-allowed',
          )}
        >
          {checking ? (
            <>
              <Loader2 size={14} className="animate-spin" />
              Checking…
            </>
          ) : (
            <>
              <Package size={14} />
              Check Compliance
            </>
          )}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="mt-4 flex items-center gap-2 px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          <XCircle size={14} />
          {error}
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="mt-4 space-y-4">
          {/* Banner */}
          <div className={clsx(
            'flex items-center gap-3 px-5 py-4 rounded-xl border text-base font-bold',
            result.approved
              ? 'bg-green-50 border-green-200 text-green-700'
              : 'bg-red-50 border-red-200 text-red-700',
          )}>
            {result.approved ? (
              <CheckCircle2 size={20} />
            ) : (
              <XCircle size={20} />
            )}
            {result.approved ? 'APPROVED — Shipment is compliant' : 'BLOCKED — Compliance issues detected'}
          </div>

          {/* Issues */}
          {result.issues.length > 0 && (
            <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-slate-100 bg-slate-50">
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                  Issues ({result.issues.length})
                </p>
              </div>
              <div className="divide-y divide-slate-100">
                {result.issues.map((issue: ComplianceIssue, idx: number) => (
                  <div key={idx} className="flex items-start gap-3 px-5 py-3">
                    <SeverityIcon severity={issue.severity} />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-slate-800">{issue.issue}</p>
                      <p className="text-xs text-slate-500 mt-0.5">{issue.action}</p>
                      {issue.fee > 0 && (
                        <p className="text-xs text-amber-600 mt-0.5 font-medium">
                          Fee: ${issue.fee.toLocaleString()}
                        </p>
                      )}
                    </div>
                    <span className="text-xs text-slate-400 font-mono shrink-0">{issue.state_code}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Fees breakdown */}
          {feeEntries.length > 0 && (
            <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-slate-100 bg-slate-50">
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Fee Breakdown</p>
              </div>
              <table className="w-full">
                <tbody className="divide-y divide-slate-100">
                  {feeEntries.map(([key, val]) => (
                    <tr key={key} className="hover:bg-slate-50">
                      <td className="px-5 py-2.5 text-sm text-slate-700">{key}</td>
                      <td className="px-5 py-2.5 text-sm text-slate-800 font-medium text-right">
                        ${val.toLocaleString()}
                      </td>
                    </tr>
                  ))}
                  <tr className="bg-slate-50">
                    <td className="px-5 py-2.5 text-sm font-semibold text-slate-800">Total</td>
                    <td className="px-5 py-2.5 text-sm font-bold text-slate-900 text-right">
                      ${result.total_compliance_cost.toLocaleString()}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
