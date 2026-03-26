import React, { useState } from 'react';
import clsx from 'clsx';
import { FileText, Tag, Shield } from 'lucide-react';
import { StateLicense, BrandRegistration, FederalPermit } from '../../lib/api';

interface LicenseRegistryProps {
  licenses: StateLicense[];
  brandRegistrations: BrandRegistration[];
  federalPermits: FederalPermit[];
  loading: boolean;
}

type SubTab = 'licenses' | 'brands' | 'federal';

const STATUS_BADGE: Record<string, string> = {
  active:      'bg-emerald-100 text-emerald-700',
  expired:     'bg-red-100 text-red-700',
  pending:     'bg-amber-100 text-amber-700',
  revoked:     'bg-slate-100 text-slate-600',
  suspended:   'bg-orange-100 text-orange-700',
  not_required:'bg-slate-100 text-slate-500',
};

function daysUntil(dateStr: string): number | null {
  if (!dateStr) return null;
  const d = new Date(dateStr);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.floor((d.getTime() - today.getTime()) / 86_400_000);
}

function ExpiryCell({ dateStr }: { dateStr: string }) {
  const days = daysUntil(dateStr);
  if (days === null) return <span className="text-slate-400">—</span>;
  if (days < 0)  return <span className="text-red-600 font-medium">Expired</span>;
  if (days <= 14) return <span className="text-red-600 font-semibold">{dateStr} ({days}d)</span>;
  if (days <= 60) return <span className="text-amber-600">{dateStr} ({days}d)</span>;
  return <span className="text-slate-600">{dateStr}</span>;
}

function EmptyState({ label }: { label: string }) {
  return (
    <div className="py-16 text-center text-slate-400">
      <Shield size={36} className="mx-auto mb-3 text-slate-300" />
      <p className="text-sm">{label}</p>
    </div>
  );
}

export function LicenseRegistry({
  licenses,
  brandRegistrations,
  federalPermits,
  loading,
}: LicenseRegistryProps) {
  const [subTab, setSubTab] = useState<SubTab>('licenses');

  const SUB_TABS: { id: SubTab; label: string; count: number; icon: React.ElementType }[] = [
    { id: 'licenses', label: 'State Licenses',       count: licenses.length,           icon: FileText },
    { id: 'brands',   label: 'Brand Registrations',  count: brandRegistrations.length,  icon: Tag      },
    { id: 'federal',  label: 'Federal Permits',       count: federalPermits.length,      icon: Shield   },
  ];

  if (loading) {
    return (
      <div className="p-6 space-y-2">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-10 bg-slate-100 rounded animate-pulse" />
        ))}
      </div>
    );
  }

  return (
    <div>
      {/* Sub-tabs */}
      <div className="flex gap-1 px-6 pt-4 pb-0 border-b border-slate-100">
        {SUB_TABS.map(({ id, label, count, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setSubTab(id)}
            className={clsx(
              'flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-t-lg border-b-2 transition-colors',
              subTab === id
                ? 'border-blue-600 text-blue-600 bg-blue-50/50'
                : 'border-transparent text-slate-500 hover:text-slate-700 hover:bg-slate-50',
            )}
          >
            <Icon size={13} />
            {label}
            {count > 0 && (
              <span className={clsx(
                'px-1.5 py-0.5 rounded-full text-[10px] font-bold min-w-[18px] text-center leading-none',
                subTab === id ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-500',
              )}>
                {count}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="p-6">
        {/* State Licenses */}
        {subTab === 'licenses' && (
          licenses.length === 0
            ? <EmptyState label="No state licenses on file. Add licenses to track expiration and compliance status." />
            : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-slate-500 text-xs uppercase tracking-wider">
                    <th className="text-left pb-2 font-medium">State</th>
                    <th className="text-left pb-2 font-medium">License Type</th>
                    <th className="text-left pb-2 font-medium">License #</th>
                    <th className="text-left pb-2 font-medium">Expires</th>
                    <th className="text-left pb-2 font-medium">Status</th>
                    <th className="text-right pb-2 font-medium">Annual Fee</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {licenses.map((lic) => (
                    <tr key={lic.id} className="hover:bg-slate-50">
                      <td className="py-2.5 pr-4 font-semibold text-slate-800">{lic.state_code}</td>
                      <td className="py-2.5 pr-4 text-slate-600">{lic.license_type}</td>
                      <td className="py-2.5 pr-4 font-mono text-slate-500 text-xs">{lic.license_number || '—'}</td>
                      <td className="py-2.5 pr-4">
                        <ExpiryCell dateStr={lic.expiration_date} />
                      </td>
                      <td className="py-2.5 pr-4">
                        <span className={clsx(
                          'px-2 py-0.5 rounded-full text-xs font-medium',
                          STATUS_BADGE[lic.status] ?? 'bg-slate-100 text-slate-500',
                        )}>
                          {lic.status}
                        </span>
                      </td>
                      <td className="py-2.5 text-right text-slate-600">
                        {lic.annual_fee ? `$${lic.annual_fee.toLocaleString()}` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
        )}

        {/* Brand Registrations */}
        {subTab === 'brands' && (
          brandRegistrations.length === 0
            ? <EmptyState label="No brand registrations on file. Add registrations for states that require product-level approval." />
            : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-slate-500 text-xs uppercase tracking-wider">
                    <th className="text-left pb-2 font-medium">State</th>
                    <th className="text-left pb-2 font-medium">Product ID</th>
                    <th className="text-left pb-2 font-medium">Reg #</th>
                    <th className="text-left pb-2 font-medium">Expires</th>
                    <th className="text-left pb-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {brandRegistrations.map((reg) => (
                    <tr key={reg.id} className="hover:bg-slate-50">
                      <td className="py-2.5 pr-4 font-semibold text-slate-800">{reg.state_code}</td>
                      <td className="py-2.5 pr-4 font-mono text-slate-500 text-xs">{reg.product_id}</td>
                      <td className="py-2.5 pr-4 font-mono text-slate-500 text-xs">{reg.registration_number || '—'}</td>
                      <td className="py-2.5 pr-4">
                        <ExpiryCell dateStr={reg.expiration_date} />
                      </td>
                      <td className="py-2.5">
                        <span className={clsx(
                          'px-2 py-0.5 rounded-full text-xs font-medium',
                          STATUS_BADGE[reg.status] ?? 'bg-slate-100 text-slate-500',
                        )}>
                          {reg.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
        )}

        {/* Federal Permits */}
        {subTab === 'federal' && (
          federalPermits.length === 0
            ? <EmptyState label="No federal permits on file. Add your TTB Basic Permit, FDA facility registration, and CBP importer number." />
            : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-slate-500 text-xs uppercase tracking-wider">
                    <th className="text-left pb-2 font-medium">Permit Type</th>
                    <th className="text-left pb-2 font-medium">Permit #</th>
                    <th className="text-left pb-2 font-medium">Expires</th>
                    <th className="text-left pb-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {federalPermits.map((permit) => (
                    <tr key={permit.id} className="hover:bg-slate-50">
                      <td className="py-2.5 pr-4 font-medium text-slate-700 capitalize">
                        {permit.permit_type.replace(/_/g, ' ')}
                      </td>
                      <td className="py-2.5 pr-4 font-mono text-slate-500 text-xs">{permit.permit_number || '—'}</td>
                      <td className="py-2.5 pr-4">
                        {permit.expiration_date
                          ? <ExpiryCell dateStr={permit.expiration_date} />
                          : <span className="text-emerald-600 text-xs">No expiration</span>
                        }
                      </td>
                      <td className="py-2.5">
                        <span className={clsx(
                          'px-2 py-0.5 rounded-full text-xs font-medium',
                          STATUS_BADGE[permit.status] ?? 'bg-slate-100 text-slate-500',
                        )}>
                          {permit.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
        )}
      </div>
    </div>
  );
}
