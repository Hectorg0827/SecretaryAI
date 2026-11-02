import React, { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import {
  BUTTON_PRIMARY, BUTTON_SECONDARY, CARD, ConfirmPanel, ErrorNote, Field, INPUT,
  parseCases, useConfirmedWrite,
} from './logisticsShared';

type Warehouse = { id: string; code: string; name: string };
type LineDraft = { sku: string; cases: string };
export type CreatedPurchaseOrder = {
  po_id: string; po_number: string; status: string; line_count: number; inventory_posted: boolean;
};
type OrderBody = {
  po_number: string;
  supplier_ref: string;
  warehouse_id: string;
  lines: { sku: string; qty_ordered: number }[];
};

function buildBody(poNumber: string, supplier: string, warehouseId: string, lines: LineDraft[]):
  { body: OrderBody | null; problem: string } {
  const po = poNumber.trim();
  const supplierRef = supplier.trim();
  if (!po || po.length > 100) return { body: null, problem: 'Enter a purchase order number (up to 100 characters).' };
  if (!supplierRef || supplierRef.length > 200) return { body: null, problem: 'Enter the supplier (up to 200 characters).' };
  if (!warehouseId) return { body: null, problem: 'Choose the warehouse the goods will be delivered to.' };
  if (lines.length === 0) return { body: null, problem: 'Add at least one product line.' };
  const seen = new Set<string>();
  const parsed: { sku: string; qty_ordered: number }[] = [];
  for (const [index, line] of lines.entries()) {
    const sku = line.sku.trim();
    const qty = parseCases(line.cases, { allowZero: false });
    if (!sku || sku.length > 120) return { body: null, problem: `Line ${index + 1}: enter the product SKU.` };
    if (qty === null) return { body: null, problem: `Line ${index + 1}: enter a whole number of cases (at least 1).` };
    if (seen.has(sku)) return { body: null, problem: `SKU ${sku} is listed more than once; combine it into one line.` };
    seen.add(sku);
    parsed.push({ sku, qty_ordered: qty });
  }
  return { body: { po_number: po, supplier_ref: supplierRef, warehouse_id: warehouseId, lines: parsed }, problem: '' };
}

/** Save a DRAFT purchase order (POST /api/logistics/orders) after explicit confirmation. */
export function CreatePurchaseOrderForm({ onCreated, onCancel }: {
  onCreated: (created: CreatedPurchaseOrder) => void;
  onCancel: () => void;
}) {
  const [warehouses, setWarehouses] = useState<Warehouse[] | null>(null);
  const [warehouseError, setWarehouseError] = useState<unknown>(null);
  const [poNumber, setPoNumber] = useState('');
  const [supplier, setSupplier] = useState('');
  const [warehouseId, setWarehouseId] = useState('');
  const [lines, setLines] = useState<LineDraft[]>([{ sku: '', cases: '' }]);
  const [problem, setProblem] = useState('');
  const [pending, setPending] = useState<OrderBody | null>(null);
  const write = useConfirmedWrite<CreatedPurchaseOrder>();

  useEffect(() => {
    let current = true;
    api.get<{ warehouses: Warehouse[] }>('/api/logistics/warehouses').then((data) => {
      if (!Array.isArray(data?.warehouses)) throw new Error('Warehouse list is unavailable.');
      if (current) setWarehouses(data.warehouses);
    }).catch((reason: unknown) => { if (current) setWarehouseError(reason); });
    return () => { current = false; };
  }, []);

  function updateLine(index: number, change: Partial<LineDraft>) {
    setLines((previous) => previous.map((line, i) => (i === index ? { ...line, ...change } : line)));
  }

  function review() {
    const { body, problem: issue } = buildBody(poNumber, supplier, warehouseId, lines);
    setProblem(issue);
    setPending(body);
    if (body) write.open();
  }

  async function confirm() {
    if (!pending) return;
    const created = await write.send(() => api.post<CreatedPurchaseOrder>('/api/logistics/orders', pending));
    if (created) onCreated(created);
  }

  const warehouse = warehouses?.find((item) => item.id === pending?.warehouse_id);
  const totalCases = pending?.lines.reduce((sum, line) => sum + line.qty_ordered, 0) ?? 0;

  return <form className={CARD} aria-label="New draft purchase order"
    onSubmit={(event) => { event.preventDefault(); review(); }}>
    <h3 className="font-semibold text-slate-900">New draft purchase order</h3>
    <p className="text-sm text-slate-600">
      Saves a draft in SecretaryAI only. Nothing is sent to the supplier or QuickBooks.
    </p>
    {warehouseError ? <ErrorNote error={warehouseError} fallback="Unable to load warehouses." /> :
      warehouses === null ? <p role="status" className="text-sm">Loading warehouses…</p> :
      warehouses.length === 0 ? <p className="text-sm text-slate-700">No warehouses are set up yet, so a purchase order cannot be created.</p> :
      <>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <Field label="Purchase order number" htmlFor="new-po-number">
            <input id="new-po-number" className={INPUT} value={poNumber} maxLength={100}
              onChange={(event) => setPoNumber(event.target.value)} />
          </Field>
          <Field label="Supplier" htmlFor="new-po-supplier">
            <input id="new-po-supplier" className={INPUT} value={supplier} maxLength={200}
              onChange={(event) => setSupplier(event.target.value)} />
          </Field>
          <Field label="Deliver to warehouse" htmlFor="new-po-warehouse">
            <select id="new-po-warehouse" className={INPUT} value={warehouseId}
              onChange={(event) => setWarehouseId(event.target.value)}>
              <option value="">Choose a warehouse</option>
              {warehouses.map((item) => <option key={item.id} value={item.id}>{item.code} · {item.name}</option>)}
            </select>
          </Field>
        </div>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-slate-700">Products (cases)</legend>
          {lines.map((line, index) => <div key={index} className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-[10rem]">
              <label className="text-xs text-slate-600" htmlFor={`new-po-sku-${index}`}>SKU</label>
              <input id={`new-po-sku-${index}`} className={INPUT} value={line.sku} maxLength={120}
                onChange={(event) => updateLine(index, { sku: event.target.value })} />
            </div>
            <div className="w-32">
              <label className="text-xs text-slate-600" htmlFor={`new-po-cases-${index}`}>Cases</label>
              <input id={`new-po-cases-${index}`} className={INPUT} inputMode="numeric" value={line.cases}
                onChange={(event) => updateLine(index, { cases: event.target.value })} />
            </div>
            {lines.length > 1 && <button type="button" className={BUTTON_SECONDARY}
              onClick={() => setLines((previous) => previous.filter((_, i) => i !== index))}>Remove</button>}
          </div>)}
          {lines.length < 100 && <button type="button" className={BUTTON_SECONDARY}
            onClick={() => setLines((previous) => [...previous, { sku: '', cases: '' }])}>Add product</button>}
        </fieldset>
        {problem && <p role="alert" className="text-sm text-red-700">{problem}</p>}
        {!write.confirming && <div className="flex gap-2">
          <button type="submit" className={BUTTON_PRIMARY}>Review draft</button>
          <button type="button" className={BUTTON_SECONDARY} onClick={onCancel}>Close</button>
        </div>}
      </>}
    {write.confirming && pending && <ConfirmPanel title="Save this draft purchase order?"
      confirmLabel="Save draft" busy={write.busy} onConfirm={confirm} onCancel={write.cancel}>
      <p>
        Purchase order <strong>{pending.po_number}</strong> for <strong>{pending.supplier_ref}</strong>,
        delivered to <strong>{warehouse ? `${warehouse.code} · ${warehouse.name}` : 'the chosen warehouse'}</strong>:
        {' '}{pending.lines.length} product line{pending.lines.length === 1 ? '' : 's'}, {totalCases} cases in total.
      </p>
      <p>
        This only saves a draft in SecretaryAI. It is not sent to the supplier, does not change stock and is not
        written to QuickBooks. A different owner or manager must approve the draft before deliveries can be recorded.
        Product lines cannot be edited after saving.
      </p>
    </ConfirmPanel>}
    {Boolean(write.error) && <ErrorNote error={write.error} fallback="The draft purchase order was not saved." />}
  </form>;
}
