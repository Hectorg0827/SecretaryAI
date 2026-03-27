import React, { useState } from 'react';
import { CheckCircle2, ChevronRight, ChevronLeft, Plus, Trash2, X } from 'lucide-react';
import { api } from '../../lib/api';
import toast from 'react-hot-toast';

interface Props {
  onComplete: () => void;
}

// ─── Step data shapes ─────────────────────────────────────────────────────────

interface FederalPermitForm {
  permit_type: string;
  permit_number: string;
  issue_date: string;
  expiration_date: string;
}

interface ProductForm {
  sku: string;
  name: string;
  product_type: string;
  abv_pct: string;
  container_size_ml: string;
  country_of_origin: string;
}

interface LicenseForm {
  state_code: string;
  license_type: string;
  product_types: string[];
  license_number: string;
  expiration_date: string;
}

// ─── Shared input styles ──────────────────────────────────────────────────────

const inputCls =
  'w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent placeholder:text-slate-300';
const labelCls = 'block text-xs font-medium text-slate-600 mb-1';
const selectCls = inputCls + ' bg-white';

// ─── Step components ──────────────────────────────────────────────────────────

function StepWelcome() {
  return (
    <div className="text-center py-6">
      <div className="w-16 h-16 rounded-2xl bg-blue-50 flex items-center justify-center mx-auto mb-5">
        <CheckCircle2 size={32} className="text-blue-600" />
      </div>
      <h2 className="text-2xl font-bold text-slate-900 mb-3">Let's set up your compliance profile</h2>
      <p className="text-slate-500 text-base leading-relaxed mb-2">
        We'll walk you through adding your federal permits, products, and state licenses in just a few steps.
      </p>
      <p className="text-slate-400 text-sm">
        You can always add or edit this information later from the Licenses tab.
      </p>
    </div>
  );
}

function StepFederalPermit({
  form,
  onChange,
  error,
}: {
  form: FederalPermitForm;
  onChange: (f: FederalPermitForm) => void;
  error: string | null;
}) {
  return (
    <div>
      <h2 className="text-lg font-bold text-slate-900 mb-1">Federal Permit</h2>
      <p className="text-sm text-slate-400 mb-5">Enter your TTB Basic Permit or other federal authorization.</p>
      {error && (
        <div className="mb-4 bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>
      )}
      <div className="grid grid-cols-2 gap-4">
        <div className="col-span-2">
          <label className={labelCls}>Permit Type</label>
          <select
            className={selectCls}
            value={form.permit_type}
            onChange={(e) => onChange({ ...form, permit_type: e.target.value })}
          >
            <option value="">Select type…</option>
            <option value="importer">Importer</option>
            <option value="wholesaler">Wholesaler</option>
            <option value="importer_wholesaler">Importer / Wholesaler</option>
            <option value="producer">Producer</option>
          </select>
        </div>
        <div className="col-span-2">
          <label className={labelCls}>Permit Number <span className="text-red-500">*</span></label>
          <input
            className={inputCls}
            type="text"
            placeholder="e.g. BWN-XXXXXXXXXX"
            value={form.permit_number}
            onChange={(e) => onChange({ ...form, permit_number: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Issue Date</label>
          <input
            className={inputCls}
            type="date"
            value={form.issue_date}
            onChange={(e) => onChange({ ...form, issue_date: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Expiration Date</label>
          <input
            className={inputCls}
            type="date"
            value={form.expiration_date}
            onChange={(e) => onChange({ ...form, expiration_date: e.target.value })}
          />
        </div>
      </div>
    </div>
  );
}

function ProductRow({
  product,
  index,
  onChange,
  onRemove,
  canRemove,
}: {
  product: ProductForm;
  index: number;
  onChange: (p: ProductForm) => void;
  onRemove: () => void;
  canRemove: boolean;
}) {
  return (
    <div className="border border-slate-100 rounded-xl p-4 bg-slate-50 relative">
      {canRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="absolute top-3 right-3 text-slate-300 hover:text-red-500 transition-colors"
        >
          <Trash2 size={14} />
        </button>
      )}
      <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Product {index + 1}</div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={labelCls}>SKU</label>
          <input
            className={inputCls}
            type="text"
            placeholder="e.g. PROD-001"
            value={product.sku}
            onChange={(e) => onChange({ ...product, sku: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Product Name <span className="text-red-500">*</span></label>
          <input
            className={inputCls}
            type="text"
            placeholder="e.g. Chateau Reserve Cabernet"
            value={product.name}
            onChange={(e) => onChange({ ...product, name: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Product Type</label>
          <select
            className={selectCls}
            value={product.product_type}
            onChange={(e) => onChange({ ...product, product_type: e.target.value })}
          >
            <option value="">Select…</option>
            <option value="wine">Wine</option>
            <option value="spirits">Spirits</option>
            <option value="beer">Beer</option>
            <option value="malt">Malt Beverage</option>
          </select>
        </div>
        <div>
          <label className={labelCls}>ABV %</label>
          <input
            className={inputCls}
            type="number"
            step="0.1"
            min="0"
            max="100"
            placeholder="e.g. 13.5"
            value={product.abv_pct}
            onChange={(e) => onChange({ ...product, abv_pct: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Container Size (ml)</label>
          <input
            className={inputCls}
            type="number"
            placeholder="750"
            value={product.container_size_ml}
            onChange={(e) => onChange({ ...product, container_size_ml: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Country of Origin</label>
          <input
            className={inputCls}
            type="text"
            placeholder="e.g. France"
            value={product.country_of_origin}
            onChange={(e) => onChange({ ...product, country_of_origin: e.target.value })}
          />
        </div>
      </div>
    </div>
  );
}

function StepProducts({
  products,
  onChange,
  error,
}: {
  products: ProductForm[];
  onChange: (ps: ProductForm[]) => void;
  error: string | null;
}) {
  const addProduct = () =>
    onChange([
      ...products,
      { sku: '', name: '', product_type: '', abv_pct: '', container_size_ml: '750', country_of_origin: '' },
    ]);

  const updateProduct = (i: number, p: ProductForm) => {
    const next = [...products];
    next[i] = p;
    onChange(next);
  };

  const removeProduct = (i: number) => onChange(products.filter((_, idx) => idx !== i));

  return (
    <div>
      <h2 className="text-lg font-bold text-slate-900 mb-1">Your Products</h2>
      <p className="text-sm text-slate-400 mb-4">Add the products you import or distribute. At least one is required.</p>
      {error && (
        <div className="mb-4 bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>
      )}
      <div className="space-y-3 max-h-72 overflow-y-auto pr-1">
        {products.map((p, i) => (
          <ProductRow
            key={i}
            product={p}
            index={i}
            onChange={(np) => updateProduct(i, np)}
            onRemove={() => removeProduct(i)}
            canRemove={products.length > 1}
          />
        ))}
      </div>
      <button
        type="button"
        onClick={addProduct}
        className="mt-3 flex items-center gap-1.5 text-sm text-blue-600 font-medium hover:text-blue-700 transition-colors"
      >
        <Plus size={14} />
        Add Product
      </button>
    </div>
  );
}

function LicenseRow({
  license,
  index,
  onChange,
  onRemove,
  canRemove,
}: {
  license: LicenseForm;
  index: number;
  onChange: (l: LicenseForm) => void;
  onRemove: () => void;
  canRemove: boolean;
}) {
  const toggleProductType = (pt: string) => {
    const current = license.product_types;
    const next = current.includes(pt) ? current.filter((t) => t !== pt) : [...current, pt];
    onChange({ ...license, product_types: next });
  };

  return (
    <div className="border border-slate-100 rounded-xl p-4 bg-slate-50 relative">
      {canRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="absolute top-3 right-3 text-slate-300 hover:text-red-500 transition-colors"
        >
          <Trash2 size={14} />
        </button>
      )}
      <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">License {index + 1}</div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={labelCls}>State Code <span className="text-red-500">*</span></label>
          <input
            className={inputCls}
            type="text"
            maxLength={2}
            placeholder="CA"
            value={license.state_code}
            onChange={(e) => onChange({ ...license, state_code: e.target.value.toUpperCase() })}
          />
        </div>
        <div>
          <label className={labelCls}>License Type <span className="text-red-500">*</span></label>
          <input
            className={inputCls}
            type="text"
            placeholder="e.g. Importer License"
            value={license.license_type}
            onChange={(e) => onChange({ ...license, license_type: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>License Number</label>
          <input
            className={inputCls}
            type="text"
            placeholder="e.g. LIC-12345"
            value={license.license_number}
            onChange={(e) => onChange({ ...license, license_number: e.target.value })}
          />
        </div>
        <div>
          <label className={labelCls}>Expiration Date</label>
          <input
            className={inputCls}
            type="date"
            value={license.expiration_date}
            onChange={(e) => onChange({ ...license, expiration_date: e.target.value })}
          />
        </div>
        <div className="col-span-2">
          <label className={labelCls}>Product Types Covered</label>
          <div className="flex gap-4 mt-1">
            {['wine', 'spirits', 'beer'].map((pt) => (
              <label key={pt} className="flex items-center gap-1.5 text-sm text-slate-600 cursor-pointer">
                <input
                  type="checkbox"
                  checked={license.product_types.includes(pt)}
                  onChange={() => toggleProductType(pt)}
                  className="rounded border-slate-300 text-blue-600 focus:ring-blue-400"
                />
                {pt.charAt(0).toUpperCase() + pt.slice(1)}
              </label>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function StepLicenses({
  licenses,
  onChange,
  error,
}: {
  licenses: LicenseForm[];
  onChange: (ls: LicenseForm[]) => void;
  error: string | null;
}) {
  const addLicense = () =>
    onChange([
      ...licenses,
      { state_code: '', license_type: '', product_types: [], license_number: '', expiration_date: '' },
    ]);

  const updateLicense = (i: number, l: LicenseForm) => {
    const next = [...licenses];
    next[i] = l;
    onChange(next);
  };

  const removeLicense = (i: number) => onChange(licenses.filter((_, idx) => idx !== i));

  return (
    <div>
      <h2 className="text-lg font-bold text-slate-900 mb-1">State Licenses</h2>
      <p className="text-sm text-slate-400 mb-4">Add the state licenses you hold. At least one is required.</p>
      {error && (
        <div className="mb-4 bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>
      )}
      <div className="space-y-3 max-h-72 overflow-y-auto pr-1">
        {licenses.map((l, i) => (
          <LicenseRow
            key={i}
            license={l}
            index={i}
            onChange={(nl) => updateLicense(i, nl)}
            onRemove={() => removeLicense(i)}
            canRemove={licenses.length > 1}
          />
        ))}
      </div>
      <button
        type="button"
        onClick={addLicense}
        className="mt-3 flex items-center gap-1.5 text-sm text-blue-600 font-medium hover:text-blue-700 transition-colors"
      >
        <Plus size={14} />
        Add License
      </button>
    </div>
  );
}

function StepDone({
  permit,
  products,
  licenses,
}: {
  permit: FederalPermitForm;
  products: ProductForm[];
  licenses: LicenseForm[];
}) {
  return (
    <div className="text-center py-4">
      <div className="w-16 h-16 rounded-2xl bg-emerald-50 flex items-center justify-center mx-auto mb-5">
        <CheckCircle2 size={32} className="text-emerald-600" />
      </div>
      <h2 className="text-2xl font-bold text-slate-900 mb-2">Setup complete!</h2>
      <p className="text-slate-500 text-sm mb-6">Your compliance profile has been saved. Here's a summary:</p>
      <div className="text-left bg-slate-50 rounded-xl p-5 space-y-3 text-sm">
        <div className="flex items-center justify-between">
          <span className="text-slate-500">Federal Permit</span>
          <span className="font-medium text-slate-800">
            {permit.permit_number
              ? `${permit.permit_number}${permit.permit_type ? ` (${permit.permit_type.replace(/_/g, ' ')})` : ''}`
              : 'Skipped'}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-slate-500">Products Added</span>
          <span className="font-medium text-slate-800">{products.filter((p) => p.name).length}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-slate-500">State Licenses Added</span>
          <span className="font-medium text-slate-800">{licenses.filter((l) => l.state_code).length}</span>
        </div>
      </div>
    </div>
  );
}

// ─── Progress bar ─────────────────────────────────────────────────────────────

function ProgressBar({ step, total }: { step: number; total: number }) {
  const pct = Math.round(((step) / (total - 1)) * 100);
  return (
    <div className="mb-6">
      <div className="flex justify-between text-xs text-slate-400 mb-1.5">
        <span>Step {step + 1} of {total}</span>
        <span>{pct}%</span>
      </div>
      <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-500 rounded-full transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ─── Main wizard ──────────────────────────────────────────────────────────────

const TOTAL_STEPS = 5;

export function ComplianceSetupWizard({ onComplete }: Props) {
  const [step, setStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const [stepError, setStepError] = useState<string | null>(null);

  const [permit, setPermit] = useState<FederalPermitForm>({
    permit_type: '',
    permit_number: '',
    issue_date: '',
    expiration_date: '',
  });

  const [products, setProducts] = useState<ProductForm[]>([
    { sku: '', name: '', product_type: '', abv_pct: '', container_size_ml: '750', country_of_origin: '' },
  ]);

  const [licenses, setLicenses] = useState<LicenseForm[]>([
    { state_code: '', license_type: '', product_types: [], license_number: '', expiration_date: '' },
  ]);

  async function handleNext() {
    setStepError(null);

    // Step 1 — save federal permit
    if (step === 1) {
      if (!permit.permit_number.trim()) {
        setStepError('Permit number is required.');
        return;
      }
      setSaving(true);
      try {
        const body: Record<string, unknown> = { permit_number: permit.permit_number };
        if (permit.permit_type) body.permit_type = permit.permit_type;
        if (permit.issue_date) body.issue_date = permit.issue_date;
        if (permit.expiration_date) body.expiration_date = permit.expiration_date;
        await api.compliance.createFederalPermit(body);
        toast.success('Federal permit saved.');
      } catch (err) {
        setStepError(err instanceof Error ? err.message : 'Failed to save permit.');
        setSaving(false);
        return;
      }
      setSaving(false);
    }

    // Step 2 — save products
    if (step === 2) {
      const validProducts = products.filter((p) => p.name.trim());
      if (validProducts.length === 0) {
        setStepError('Please add at least one product with a name.');
        return;
      }
      setSaving(true);
      try {
        for (const p of validProducts) {
          const body: Record<string, unknown> = { name: p.name };
          if (p.sku) body.sku = p.sku;
          if (p.product_type) body.product_type = p.product_type;
          if (p.abv_pct) body.abv_pct = parseFloat(p.abv_pct);
          if (p.container_size_ml) body.container_size_ml = parseInt(p.container_size_ml, 10);
          if (p.country_of_origin) body.country_of_origin = p.country_of_origin;
          await api.compliance.createProduct(body);
        }
        toast.success(`${validProducts.length} product(s) saved.`);
      } catch (err) {
        setStepError(err instanceof Error ? err.message : 'Failed to save products.');
        setSaving(false);
        return;
      }
      setSaving(false);
    }

    // Step 3 — save licenses
    if (step === 3) {
      const validLicenses = licenses.filter((l) => l.state_code.trim() && l.license_type.trim());
      if (validLicenses.length === 0) {
        setStepError('Please add at least one license with state code and license type.');
        return;
      }
      setSaving(true);
      try {
        for (const l of validLicenses) {
          const body: Record<string, unknown> = {
            state_code: l.state_code,
            license_type: l.license_type,
          };
          if (l.license_number) body.license_number = l.license_number;
          if (l.expiration_date) body.expiration_date = l.expiration_date;
          if (l.product_types.length > 0) body.product_types = l.product_types;
          await api.compliance.createLicense(body);
        }
        toast.success(`${validLicenses.length} license(s) saved.`);
      } catch (err) {
        setStepError(err instanceof Error ? err.message : 'Failed to save licenses.');
        setSaving(false);
        return;
      }
      setSaving(false);
    }

    if (step < TOTAL_STEPS - 1) {
      setStep(step + 1);
    }
  }

  function handleBack() {
    setStepError(null);
    setStep(step - 1);
  }

  const isLastStep = step === TOTAL_STEPS - 1;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-xl bg-white rounded-2xl p-8 shadow-2xl">
        <ProgressBar step={step} total={TOTAL_STEPS} />

        <div className="min-h-[320px]">
          {step === 0 && <StepWelcome />}
          {step === 1 && <StepFederalPermit form={permit} onChange={setPermit} error={stepError} />}
          {step === 2 && <StepProducts products={products} onChange={setProducts} error={stepError} />}
          {step === 3 && <StepLicenses licenses={licenses} onChange={setLicenses} error={stepError} />}
          {step === 4 && <StepDone permit={permit} products={products} licenses={licenses} />}
        </div>

        <div className="flex items-center justify-between mt-8 pt-4 border-t border-slate-100">
          <button
            type="button"
            onClick={handleBack}
            disabled={step === 0}
            className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium text-slate-500 hover:text-slate-700 disabled:opacity-0 transition-colors"
          >
            <ChevronLeft size={16} />
            Back
          </button>

          {isLastStep ? (
            <button
              type="button"
              onClick={onComplete}
              className="flex items-center gap-2 px-6 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-sm font-semibold transition-colors"
            >
              <CheckCircle2 size={16} />
              Go to Compliance Dashboard
            </button>
          ) : (
            <button
              type="button"
              onClick={handleNext}
              disabled={saving}
              className="flex items-center gap-1.5 px-6 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white rounded-lg text-sm font-semibold transition-colors"
            >
              {saving ? 'Saving…' : step === 0 ? 'Get Started' : 'Next'}
              {!saving && <ChevronRight size={16} />}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
