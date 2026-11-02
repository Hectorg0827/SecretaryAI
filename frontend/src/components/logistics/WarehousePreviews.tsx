import React, { useState } from 'react';
import clsx from 'clsx';
import { api } from '../../lib/api';
import { useAuth, type Role } from '../../hooks/useAuth';
import {
  BUTTON_PRIMARY, BUTTON_SECONDARY, CARD, ErrorNote, Field, INPUT, INVENTORY_VIEW_ROLES, ORDER_VIEW_ROLES,
  WarningNote, dateTimeInputToIso, nowForDateTimeInput, parseCases, roleAllowed,
} from './logisticsShared';

type ColumnKind = 'text' | 'optionalText' | 'cases' | 'positiveCases' | 'date' | 'checkbox';
type Column = { key: string; label: string; kind: ColumnKind; defaultValue?: string | boolean };
type RowGroup = { key: string; label: string; columns: Column[] };
type HeaderField = { key: string; label: string; max: number };
type Row = Record<string, string | boolean>;
type Result = Record<string, unknown>;

type PreviewConfig = {
  id: 'pick-plan' | 'cycle-count' | 'capacity' | 'claim-preview';
  title: string;
  description: string;
  roles: readonly Role[];
  headerFields: HeaderField[];
  groups: RowGroup[];
  renderResult: (result: Result) => React.ReactNode;
};

function emptyRow(columns: Column[]): Row {
  return Object.fromEntries(columns.map((column) => [
    column.key, column.defaultValue ?? (column.kind === 'checkbox' ? false : ''),
  ]));
}

function rowsToBody(group: RowGroup, rows: Row[]): { value: Record<string, unknown>[]; problem: string } {
  const value: Record<string, unknown>[] = [];
  for (const [index, row] of rows.entries()) {
    const item: Record<string, unknown> = {};
    for (const column of group.columns) {
      const raw = row[column.key];
      const where = `${group.label}, row ${index + 1}, ${column.label}`;
      if (column.kind === 'checkbox') {
        item[column.key] = raw === true;
      } else if (column.kind === 'cases' || column.kind === 'positiveCases') {
        const parsed = parseCases(String(raw ?? ''), { allowZero: column.kind === 'cases' });
        if (parsed === null) {
          return { value: [], problem: `${where}: enter a whole number${column.kind === 'positiveCases' ? ' of at least 1' : ''}.` };
        }
        item[column.key] = parsed;
      } else if (column.kind === 'date') {
        const text = String(raw ?? '').trim();
        if (text) item[column.key] = text;
      } else {
        const text = String(raw ?? '').trim();
        if (!text && column.kind === 'text') return { value: [], problem: `${where}: this is required.` };
        item[column.key] = text;
      }
    }
    value.push(item);
  }
  if (value.length === 0) return { value, problem: `${group.label}: add at least one row.` };
  return { value, problem: '' };
}

function Table({ headers, rows }: { headers: string[]; rows: React.ReactNode[][] }) {
  return <div className="overflow-x-auto"><table className="w-full text-sm text-left">
    <thead><tr className="text-slate-600">{headers.map((header) => <th key={header} className="p-2">{header}</th>)}</tr></thead>
    <tbody>{rows.map((cells, index) => <tr key={index} className="border-t border-slate-200">
      {cells.map((cell, cellIndex) => <td key={cellIndex} className="p-2">{cell}</td>)}
    </tr>)}</tbody>
  </table></div>;
}

function list<T>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

function SnapshotNotes({ result, effects }: { result: Result; effects: string }) {
  return <>
    {result.snapshot_stale === true && <WarningNote>
      The counts you entered are more than an hour old ({String(result.snapshot_age_minutes)} minutes). Check them again before acting.
    </WarningNote>}
    <p className="text-xs text-slate-600">Preview only, based on the numbers you entered (not verified). {effects}</p>
  </>;
}

const PREVIEWS: PreviewConfig[] = [
  {
    id: 'pick-plan',
    title: 'Pick plan',
    description: 'Suggests which bins to pick an order from, using the earliest expiry first and then the shortest walk.',
    roles: ORDER_VIEW_ROLES,
    headerFields: [{ key: 'order_reference', label: 'Order reference', max: 150 }],
    groups: [
      { key: 'lines', label: 'Order lines', columns: [
        { key: 'sku', label: 'SKU', kind: 'text' },
        { key: 'requested_cases', label: 'Cases', kind: 'positiveCases' },
        { key: 'require_expiry', label: 'Needs expiry date', kind: 'checkbox' },
      ] },
      { key: 'bins', label: 'Bin counts', columns: [
        { key: 'bin_id', label: 'Bin', kind: 'text' },
        { key: 'sku', label: 'SKU', kind: 'text' },
        { key: 'lot_code', label: 'Lot', kind: 'optionalText' },
        { key: 'on_hand_cases', label: 'On hand', kind: 'cases' },
        { key: 'allocated_cases', label: 'Already allocated', kind: 'cases', defaultValue: '0' },
        { key: 'quarantined_cases', label: 'On hold', kind: 'cases', defaultValue: '0' },
        { key: 'expiry_date', label: 'Expiry', kind: 'date' },
        { key: 'travel_steps', label: 'Walking steps', kind: 'cases', defaultValue: '0' },
      ] },
    ],
    renderResult: (result) => {
      const picks = list<{ sku: string; bin_id: string; lot_code: string; cases: number }>(result.picks);
      const shortages = list<{ sku: string; missing_cases: number }>(result.shortages);
      return <>
        {picks.length ? <Table headers={['SKU', 'Bin', 'Lot', 'Cases to pick']}
          rows={picks.map((pick) => [pick.sku, pick.bin_id, pick.lot_code || '—', pick.cases])} /> :
          <p className="text-sm">No bins could supply this order.</p>}
        {shortages.map((item) => <p key={item.sku} className="text-sm text-red-700">
          Short {item.missing_cases} cases of {item.sku}. Check stock before promising this order.</p>)}
        {Number(result.excluded_expired_observations) > 0 && <p className="text-sm">
          {String(result.excluded_expired_observations)} expired bin entr{Number(result.excluded_expired_observations) === 1 ? 'y was' : 'ies were'} skipped.</p>}
        <SnapshotNotes result={result} effects="No stock was reserved and no order was released." />
      </>;
    },
  },
  {
    id: 'cycle-count',
    title: 'Cycle count check',
    description: 'Compares your counted cases with what was expected and lists the bins to recount.',
    roles: ORDER_VIEW_ROLES,
    headerFields: [{ key: 'count_reference', label: 'Count sheet reference', max: 150 }],
    groups: [
      { key: 'lines', label: 'Count lines', columns: [
        { key: 'sku', label: 'SKU', kind: 'text' },
        { key: 'bin_id', label: 'Bin', kind: 'text' },
        { key: 'lot_code', label: 'Lot', kind: 'optionalText' },
        { key: 'expected_cases', label: 'Expected', kind: 'cases' },
        { key: 'counted_cases', label: 'Counted', kind: 'cases' },
      ] },
    ],
    renderResult: (result) => {
      const rows = list<{ sku: string; bin_id: string; lot_code: string; expected_cases: number; counted_cases: number; variance_cases: number }>(result.discrepancies);
      return <>
        {rows.length ? <Table headers={['SKU', 'Bin', 'Lot', 'Expected', 'Counted', 'Difference']}
          rows={rows.map((row) => [row.sku, row.bin_id, row.lot_code || '—', row.expected_cases, row.counted_cases,
            row.variance_cases > 0 ? `+${row.variance_cases}` : row.variance_cases])} /> :
          <p className="text-sm">All {String(result.line_count)} counted lines match what was expected.</p>}
        {rows.length > 0 && <p className="text-sm">Recount these bins, then have the differences approved by someone else.</p>}
        <SnapshotNotes result={result} effects="No stock adjustment was posted." />
      </>;
    },
  },
  {
    id: 'capacity',
    title: 'Warehouse space',
    description: 'Estimates how full the warehouse is and which busy bins are far to walk to.',
    roles: INVENTORY_VIEW_ROLES,
    headerFields: [],
    groups: [
      { key: 'bins', label: 'Bins', columns: [
        { key: 'bin_id', label: 'Bin', kind: 'text' },
        { key: 'capacity_cases', label: 'Capacity', kind: 'cases' },
        { key: 'occupied_cases', label: 'Occupied', kind: 'cases' },
        { key: 'reserved_cases', label: 'Reserved', kind: 'cases', defaultValue: '0' },
        { key: 'observed_pick_events', label: 'Picks counted', kind: 'cases', defaultValue: '0' },
        { key: 'travel_steps', label: 'Walking steps', kind: 'cases', defaultValue: '0' },
      ] },
    ],
    renderResult: (result) => {
      const moves = list<{ bin_id: string }>(result.candidate_moves);
      return <>
        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
          <div><dt className="text-slate-600">Capacity</dt><dd className="font-semibold">{String(result.total_capacity_cases)} cases</dd></div>
          <div><dt className="text-slate-600">Occupied</dt><dd className="font-semibold">{String(result.occupied_cases)} cases</dd></div>
          <div><dt className="text-slate-600">How full</dt><dd className="font-semibold">
            {result.physical_utilization_pct == null ? 'Unknown (no capacity entered)' : `${String(result.physical_utilization_pct)}%`}</dd></div>
          <div><dt className="text-slate-600">Over capacity</dt><dd className="font-semibold">{String(result.overcommitted_cases)} cases</dd></div>
        </dl>
        {result.slotting_confidence === 'insufficient_data' ? <p className="text-sm">
          Not enough picks counted (at least 30 needed) to suggest moving bins.</p> :
          moves.length ? <p className="text-sm">Consider moving these busy, far-away bins closer: {moves.map((move) => move.bin_id).join(', ')}.</p> :
          <p className="text-sm">No bin moves suggested.</p>}
        <SnapshotNotes result={result} effects="No bins were moved and no stock was adjusted." />
      </>;
    },
  },
  {
    id: 'claim-preview',
    title: 'Supplier claim draft',
    description: 'Works out shortages, overages and damage on a delivery to support a supplier claim.',
    roles: ORDER_VIEW_ROLES,
    headerFields: [
      { key: 'po_number', label: 'Purchase order number', max: 100 },
      { key: 'receipt_reference', label: 'Receipt reference', max: 150 },
    ],
    groups: [
      { key: 'lines', label: 'Delivery lines', columns: [
        { key: 'sku', label: 'SKU', kind: 'text' },
        { key: 'lot_code', label: 'Lot', kind: 'optionalText' },
        { key: 'expected_cases', label: 'Expected', kind: 'cases' },
        { key: 'accepted_cases', label: 'Good', kind: 'cases' },
        { key: 'damaged_cases', label: 'Damaged', kind: 'cases', defaultValue: '0' },
        { key: 'notes', label: 'Notes', kind: 'optionalText' },
      ] },
    ],
    renderResult: (result) => {
      const issues = list<{ sku: string; lot_code: string; shortage_cases: number; overage_cases: number; damaged_cases: number }>(result.issues);
      return <>
        {issues.length ? <Table headers={['SKU', 'Lot', 'Short', 'Extra', 'Damaged']}
          rows={issues.map((issue) => [issue.sku, issue.lot_code || '—', issue.shortage_cases, issue.overage_cases, issue.damaged_cases])} /> :
          <p className="text-sm">No shortages, extras or damage found in the lines entered.</p>}
        <SnapshotNotes result={result} effects="No claim was filed, the supplier was not contacted and nothing was written to your accounts." />
      </>;
    },
  },
];

function PreviewForm({ config }: { config: PreviewConfig }) {
  const [warehouse, setWarehouse] = useState('');
  const [observedAt, setObservedAt] = useState(() => nowForDateTimeInput());
  const [header, setHeader] = useState<Record<string, string>>({});
  const [rows, setRows] = useState<Record<string, Row[]>>(() => Object.fromEntries(
    config.groups.map((group) => [group.key, [emptyRow(group.columns)]])));
  const [problem, setProblem] = useState('');
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  function updateCell(groupKey: string, index: number, key: string, value: string | boolean) {
    setRows((previous) => ({
      ...previous,
      [groupKey]: previous[groupKey].map((row, i) => (i === index ? { ...row, [key]: value } : row)),
    }));
  }

  async function submit() {
    setResult(null);
    setError(null);
    const warehouseId = warehouse.trim();
    if (!warehouseId || warehouseId.length > 150) { setProblem('Enter the warehouse name or code.'); return; }
    const observed = dateTimeInputToIso(observedAt);
    if (!observed) { setProblem('Enter when the counts were taken.'); return; }
    const body: Record<string, unknown> = { warehouse_id: warehouseId, observed_at: observed, unit: 'case' };
    for (const field of config.headerFields) {
      const value = (header[field.key] ?? '').trim();
      if (!value || value.length > field.max) { setProblem(`Enter the ${field.label.toLowerCase()}.`); return; }
      body[field.key] = value;
    }
    for (const group of config.groups) {
      const converted = rowsToBody(group, rows[group.key] ?? []);
      if (converted.problem) { setProblem(converted.problem); return; }
      body[group.key] = converted.value;
    }
    setProblem('');
    setLoading(true);
    try {
      const data = await api.post<Result>(`/api/logistics/warehouse/${config.id}`, body);
      if (!data || data.status !== 'analysis_only') throw new Error('Warehouse preview is unavailable.');
      setResult(data);
    } catch (reason) {
      setError(reason);
    } finally {
      setLoading(false);
    }
  }

  return <form className={CARD} aria-label={config.title} onSubmit={(event) => { event.preventDefault(); void submit(); }}>
    <h3 className="font-semibold text-slate-900">{config.title}</h3>
    <p className="text-sm text-slate-600">{config.description} Nothing is saved or changed.</p>
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <Field label="Warehouse" htmlFor={`${config.id}-warehouse`}>
        <input id={`${config.id}-warehouse`} className={INPUT} value={warehouse} maxLength={150}
          onChange={(event) => setWarehouse(event.target.value)} />
      </Field>
      <Field label="Counts taken at" htmlFor={`${config.id}-observed`}>
        <input id={`${config.id}-observed`} type="datetime-local" className={INPUT} value={observedAt}
          onChange={(event) => setObservedAt(event.target.value)} />
      </Field>
      {config.headerFields.map((field) => <Field key={field.key} label={field.label} htmlFor={`${config.id}-${field.key}`}>
        <input id={`${config.id}-${field.key}`} className={INPUT} maxLength={field.max} value={header[field.key] ?? ''}
          onChange={(event) => setHeader((previous) => ({ ...previous, [field.key]: event.target.value }))} />
      </Field>)}
    </div>
    {config.groups.map((group) => <fieldset key={group.key} className="space-y-2">
      <legend className="text-sm font-medium text-slate-700">{group.label} (cases)</legend>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-600">{group.columns.map((column) =>
            <th key={column.key} className="p-1 font-normal">{column.label}</th>)}<th /></tr></thead>
          <tbody>{(rows[group.key] ?? []).map((row, index) => <tr key={index}>
            {group.columns.map((column) => <td key={column.key} className="p-1">
              {column.kind === 'checkbox' ?
                <input type="checkbox" aria-label={`${group.label} row ${index + 1} ${column.label}`} checked={row[column.key] === true}
                  onChange={(event) => updateCell(group.key, index, column.key, event.target.checked)} /> :
                <input aria-label={`${group.label} row ${index + 1} ${column.label}`} className={INPUT}
                  type={column.kind === 'date' ? 'date' : 'text'}
                  inputMode={column.kind === 'cases' || column.kind === 'positiveCases' ? 'numeric' : undefined}
                  value={String(row[column.key] ?? '')}
                  onChange={(event) => updateCell(group.key, index, column.key, event.target.value)} />}
            </td>)}
            <td className="p-1">{(rows[group.key] ?? []).length > 1 && <button type="button" className={BUTTON_SECONDARY}
              onClick={() => setRows((previous) => ({ ...previous, [group.key]: previous[group.key].filter((_, i) => i !== index) }))}>
              Remove</button>}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <button type="button" className={BUTTON_SECONDARY} onClick={() => setRows((previous) => ({
        ...previous, [group.key]: [...(previous[group.key] ?? []), emptyRow(group.columns)],
      }))}>Add row</button>
    </fieldset>)}
    {problem && <p role="alert" className="text-sm text-red-700">{problem}</p>}
    <button type="submit" className={BUTTON_PRIMARY} disabled={loading}>{loading ? 'Working…' : 'Show preview'}</button>
    {Boolean(error) && <ErrorNote error={error} fallback="Unable to build the warehouse preview." />}
    {result && <div className="space-y-2" aria-label={`${config.title} result`}>{config.renderResult(result)}</div>}
  </form>;
}

/** Warehouse work-order previews under /api/logistics/warehouse. Preview-only. */
export function WarehousePreviews() {
  const { role } = useAuth();
  const available = PREVIEWS.filter((preview) => roleAllowed(role, preview.roles));
  const [active, setActive] = useState<PreviewConfig['id']>(available[0]?.id ?? 'pick-plan');
  const current = available.find((preview) => preview.id === active) ?? available[0];

  if (!current) return <p className="p-4 sm:p-6 text-sm text-slate-600">Warehouse checks are not available for your role.</p>;

  return <section className="p-4 sm:p-6 space-y-4" aria-label="Warehouse checks">
    <div className="flex flex-wrap gap-2">
      {available.map((preview) => <button key={preview.id} type="button" onClick={() => setActive(preview.id)}
        className={clsx('px-3 py-1.5 rounded-lg text-sm', preview.id === current.id
          ? 'bg-slate-800 text-white' : 'border border-slate-300 text-slate-700 hover:bg-slate-50')}>
        {preview.title}</button>)}
    </div>
    <PreviewForm key={current.id} config={current} />
  </section>;
}
