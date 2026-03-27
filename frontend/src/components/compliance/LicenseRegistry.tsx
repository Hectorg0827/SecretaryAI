import React, { useState } from 'react';
import clsx from 'clsx';
import { FileText, Tag, Shield, Plus, Trash2, Upload, X } from 'lucide-react';
import { StateLicense, BrandRegistration, FederalPermit, api } from '../../lib/api';
import { CsvImportModal } from './CsvImportModal';
import toast from 'react-hot-toast';

interface LicenseRegistryProps {
  licenses: StateLicense[];
  brandRegistrations: BrandRegistration[];
  federalPermits: FederalPermit[];
  loading: boolean;
  onRefresh: () => void;
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

// ─── Shared form styles ────────────────────────────────────────────────────────

const inputCls =
  'w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent placeholder:text-slate-300';
const labelCls = 'block text-xs font-medium text-slate-600 mb-1';
const selectCls = inputCls + ' bg-white';

function ModalOverlay({ title, onClose, children, onSave, saving }: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  onSave: () => void;
  saving: boolean;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-lg bg-white rounded-2xl shadow-2xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100">
          <h3 className="text-base font-bold text-slate-900">{title}</h3>
          <button onClick={onClose} className="text-slate-300 hover:text-slate-600 transition-colors">
            <X size={18} />
          </button>
        </div>
        <div className="px-6 py-5 space-y-4 max-h-[70vh] overflow-y-auto">
          {children}
        </div>
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-slate-100 bg-slate-50 rounded-b-2xl">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-800 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onSave}
            disabled={saving}
            className="px-5 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white text-sm font-semibold rounded-lg transition-colors"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Add State License Modal ───────────────────────────────────────────────────

function AddLicenseModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    state_code: '',
    license_type: '',
    product_types: [] as string[],
    license_number: '',
    expiration_date: '',
    annual_fee: '',
    status: 'active',
  });
  const [saving, setSaving] = useState(false);

  const toggleProductType = (pt: string) => {
    const current = form.product_types;
    setForm({
      ...form,
      product_types: current.includes(pt) ? current.filter((t) => t !== pt) : [...current, pt],
    });
  };

  async function handleSave() {
    if (!form.state_code.trim() || !form.license_type.trim()) {
      toast.error('State code and license type are required.');
      return;
    }
    setSaving(true);
    try {
      const body: Record<string, unknown> = {
        state_code: form.state_code.toUpperCase(),
        license_type: form.license_type,
        status: form.status,
      };
      if (form.license_number) body.license_number = form.license_number;
      if (form.expiration_date) body.expiration_date = form.expiration_date;
      if (form.annual_fee) body.annual_fee = parseFloat(form.annual_fee);
      if (form.product_types.length > 0) body.product_types = form.product_types;
      await api.compliance.createLicense(body);
      toast.success('License saved.');
      onSaved();
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save license.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <ModalOverlay title="Add State License" onClose={onClose} onSave={handleSave} saving={saving}>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={labelCls}>State Code <span className="text-red-500">*</span></label>
          <input
            className={inputCls}
            type="text"
            maxLength={2}
            placeholder="CA"
            value={form.state_code}
            onChange={(e) => setForm({ ...form, state_code: e.target.value.toUpperCase() })}
          />
        </div>
        <div>
          <label className={labelCls}>License Type <span className="text-red-500">*</span></label>
          <input
            className={inputCls}
            type="text"
            placeholder="e.g. Importer License"
            value={form.license_type}
            onChange={(e) => setForm({ ...form, license_type: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>License Number</label>
          <input
            className={inputCls}
            type="text"
            placeholder="e.g. LIC-12345"
            value={form.license_number}
            onChange={(e) => setForm({ ...form, license_number: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Expiration Date</label>
          <input
            className={inputCls}
            type="date"
            value={form.expiration_date}
            onChange={(e) => setForm({ ...form, expiration_date: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Annual Fee ($)</label>
          <input
            className={inputCls}
            type="number"
            min="0"
            step="0.01"
            placeholder="0.00"
            value={form.annual_fee}
            onChange={(e) => setForm({ ...form, annual_fee: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Status</label>
          <select
            className={selectCls}
            value={form.status}
            onChange={(e) => setForm({ ...form, status: e.target.value })}
          >
            <option value="active">Active</option>
            <option value="pending">Pending</option>
            <option value="expired">Expired</option>
            <option value="suspended">Suspended</option>
            <option value="revoked">Revoked</option>
          </select>
        </div>
        <div className="col-span-2">
          <label className={labelCls}>Product Types Covered</label>
          <div className="flex gap-4 mt-1">
            {['wine', 'spirits', 'beer'].map((pt) => (
              <label key={pt} className="flex items-center gap-1.5 text-sm text-slate-600 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.product_types.includes(pt)}
                  onChange={() => toggleProductType(pt)}
                  className="rounded border-slate-300 text-blue-600 focus:ring-blue-400"
                />
                {pt.charAt(0).toUpperCase() + pt.slice(1)}
              </label>
            ))}
          </div>
        </div>
      </div>
    </ModalOverlay>
  );
}

// ─── Add Brand Registration Modal ─────────────────────────────────────────────

function AddBrandRegistrationModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    product_id: '',
    state_code: '',
    registration_number: '',
    expiration_date: '',
    registration_fee: '',
    status: 'active',
  });
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!form.state_code.trim()) {
      toast.error('State code is required.');
      return;
    }
    setSaving(true);
    try {
      const body: Record<string, unknown> = {
        state_code: form.state_code.toUpperCase(),
        status: form.status,
      };
      if (form.product_id) body.product_id = form.product_id;
      if (form.registration_number) body.registration_number = form.registration_number;
      if (form.expiration_date) body.expiration_date = form.expiration_date;
      if (form.registration_fee) body.registration_fee = parseFloat(form.registration_fee);
      await api.compliance.createBrandRegistration(body);
      toast.success('Brand registration saved.');
      onSaved();
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save brand registration.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <ModalOverlay title="Add Brand Registration" onClose={onClose} onSave={handleSave} saving={saving}>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={labelCls}>State Code <span className="text-red-500">*</span></label>
          <input
            className={inputCls}
            type="text"
            maxLength={2}
            placeholder="CA"
            value={form.state_code}
            onChange={(e) => setForm({ ...form, state_code: e.target.value.toUpperCase() })}
          />
        </div>
        <div>
          <label className={labelCls}>Product ID</label>
          <input
            className={inputCls}
            type="text"
            placeholder="e.g. prod-uuid or SKU"
            value={form.product_id}
            onChange={(e) => setForm({ ...form, product_id: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Registration Number</label>
          <input
            className={inputCls}
            type="text"
            placeholder="e.g. BR-2024-001"
            value={form.registration_number}
            onChange={(e) => setForm({ ...form, registration_number: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Expiration Date</label>
          <input
            className={inputCls}
            type="date"
            value={form.expiration_date}
            onChange={(e) => setForm({ ...form, expiration_date: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Registration Fee ($)</label>
          <input
            className={inputCls}
            type="number"
            min="0"
            step="0.01"
            placeholder="0.00"
            value={form.registration_fee}
            onChange={(e) => setForm({ ...form, registration_fee: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Status</label>
          <select
            className={selectCls}
            value={form.status}
            onChange={(e) => setForm({ ...form, status: e.target.value })}
          >
            <option value="active">Active</option>
            <option value="pending">Pending</option>
            <option value="expired">Expired</option>
            <option value="suspended">Suspended</option>
            <option value="revoked">Revoked</option>
          </select>
        </div>
      </div>
    </ModalOverlay>
  );
}

// ─── Add Federal Permit Modal ──────────────────────────────────────────────────

function AddFederalPermitModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    permit_type: '',
    permit_number: '',
    expiration_date: '',
    status: 'active',
  });
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!form.permit_number.trim()) {
      toast.error('Permit number is required.');
      return;
    }
    setSaving(true);
    try {
      const body: Record<string, unknown> = {
        permit_number: form.permit_number,
        status: form.status,
      };
      if (form.permit_type) body.permit_type = form.permit_type;
      if (form.expiration_date) body.expiration_date = form.expiration_date;
      await api.compliance.createFederalPermit(body);
      toast.success('Federal permit saved.');
      onSaved();
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save federal permit.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <ModalOverlay title="Add Federal Permit" onClose={onClose} onSave={handleSave} saving={saving}>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={labelCls}>Permit Type</label>
          <select
            className={selectCls}
            value={form.permit_type}
            onChange={(e) => setForm({ ...form, permit_type: e.target.value })}
          >
            <option value="">Select type…</option>
            <option value="importer">Importer</option>
            <option value="wholesaler">Wholesaler</option>
            <option value="importer_wholesaler">Importer / Wholesaler</option>
            <option value="producer">Producer</option>
          </select>
        </div>
        <div>
          <label className={labelCls}>Permit Number <span className="text-red-500">*</span></label>
          <input
            className={inputCls}
            type="text"
            placeholder="e.g. BWN-XXXXXXXXXX"
            value={form.permit_number}
            onChange={(e) => setForm({ ...form, permit_number: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Expiration Date</label>
          <input
            className={inputCls}
            type="date"
            value={form.expiration_date}
            onChange={(e) => setForm({ ...form, expiration_date: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Status</label>
          <select
            className={selectCls}
            value={form.status}
            onChange={(e) => setForm({ ...form, status: e.target.value })}
          >
            <option value="active">Active</option>
            <option value="pending">Pending</option>
            <option value="expired">Expired</option>
            <option value="suspended">Suspended</option>
            <option value="revoked">Revoked</option>
          </select>
        </div>
      </div>
    </ModalOverlay>
  );
}

export function LicenseRegistry({
  licenses,
  brandRegistrations,
  federalPermits,
  loading,
  onRefresh,
}: LicenseRegistryProps) {
  const [subTab, setSubTab] = useState<SubTab>('licenses');
  const [showAddLicense, setShowAddLicense] = useState(false);
  const [showAddBrand, setShowAddBrand] = useState(false);
  const [showAddPermit, setShowAddPermit] = useState(false);
  const [csvEntityType, setCsvEntityType] = useState<string | null>(null);

  const SUB_TABS: { id: SubTab; label: string; count: number; icon: React.ElementType }[] = [
    { id: 'licenses', label: 'State Licenses',       count: licenses.length,           icon: FileText },
    { id: 'brands',   label: 'Brand Registrations',  count: brandRegistrations.length,  icon: Tag      },
    { id: 'federal',  label: 'Federal Permits',       count: federalPermits.length,      icon: Shield   },
  ];

  async function handleDeleteLicense(id: string) {
    if (!confirm('Delete this license?')) return;
    try {
      await api.compliance.deleteLicense(id);
      toast.success('License deleted.');
      onRefresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete license.');
    }
  }

  async function handleDeleteBrandRegistration(id: string) {
    if (!confirm('Delete this brand registration?')) return;
    try {
      await api.compliance.deleteBrandRegistration(id);
      toast.success('Brand registration deleted.');
      onRefresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete brand registration.');
    }
  }

  async function handleDeleteFederalPermit(id: string) {
    if (!confirm('Delete this federal permit?')) return;
    try {
      await api.compliance.deleteFederalPermit(id);
      toast.success('Federal permit deleted.');
      onRefresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete federal permit.');
    }
  }

  if (loading) {
    return (
      <div className="p-6 space-y-2">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-10 bg-slate-100 rounded animate-pulse" />
        ))}
      </div>
    );
  }

  const csvEntityMap: Record<SubTab, string> = {
    licenses: 'licenses',
    brands: 'brand-registrations',
    federal: 'federal-permits',
  };

  return (
    <div>
      {/* Modals */}
      {showAddLicense && (
        <AddLicenseModal
          onClose={() => setShowAddLicense(false)}
          onSaved={onRefresh}
        />
      )}
      {showAddBrand && (
        <AddBrandRegistrationModal
          onClose={() => setShowAddBrand(false)}
          onSaved={onRefresh}
        />
      )}
      {showAddPermit && (
        <AddFederalPermitModal
          onClose={() => setShowAddPermit(false)}
          onSaved={onRefresh}
        />
      )}
      {csvEntityType && (
        <CsvImportModal
          entityType={csvEntityType}
          onImported={onRefresh}
          onClose={() => setCsvEntityType(null)}
        />
      )}

      {/* Sub-tabs */}
      <div className="flex items-center gap-1 px-6 pt-4 pb-0 border-b border-slate-100">
        <div className="flex gap-1 flex-1">
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

        {/* Action buttons for current sub-tab */}
        <div className="flex items-center gap-2 pb-1">
          <button
            onClick={() => setCsvEntityType(csvEntityMap[subTab])}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-500 hover:text-slate-700 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
          >
            <Upload size={12} />
            Import CSV
          </button>
          <button
            onClick={() => {
              if (subTab === 'licenses') setShowAddLicense(true);
              else if (subTab === 'brands') setShowAddBrand(true);
              else if (subTab === 'federal') setShowAddPermit(true);
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors"
          >
            <Plus size={12} />
            Add
          </button>
        </div>
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
                    <th className="pb-2 w-8" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {licenses.map((lic) => (
                    <tr key={lic.id} className="hover:bg-slate-50 group">
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
                      <td className="py-2.5 pl-2">
                        <button
                          onClick={() => handleDeleteLicense(lic.id)}
                          className="opacity-0 group-hover:opacity-100 text-slate-300 hover:text-red-500 transition-all"
                          title="Delete"
                        >
                          <Trash2 size={13} />
                        </button>
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
                    <th className="pb-2 w-8" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {brandRegistrations.map((reg) => (
                    <tr key={reg.id} className="hover:bg-slate-50 group">
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
                      <td className="py-2.5 pl-2">
                        <button
                          onClick={() => handleDeleteBrandRegistration(reg.id)}
                          className="opacity-0 group-hover:opacity-100 text-slate-300 hover:text-red-500 transition-all"
                          title="Delete"
                        >
                          <Trash2 size={13} />
                        </button>
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
                    <th className="pb-2 w-8" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {federalPermits.map((permit) => (
                    <tr key={permit.id} className="hover:bg-slate-50 group">
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
                      <td className="py-2.5 pl-2">
                        <button
                          onClick={() => handleDeleteFederalPermit(permit.id)}
                          className="opacity-0 group-hover:opacity-100 text-slate-300 hover:text-red-500 transition-all"
                          title="Delete"
                        >
                          <Trash2 size={13} />
                        </button>
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
