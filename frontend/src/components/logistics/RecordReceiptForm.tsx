import React, { useId, useState } from 'react';
import { api } from '../../lib/api';
import {
  BUTTON_PRIMARY, CARD, ConfirmPanel, ErrorNote, Field, INPUT, parseCases, useConfirmedWrite,
} from './logisticsShared';

export type PurchaseLine = { sku: string; qty_ordered: number | string; qty_received: number | string };
type ReceiptLineDraft = { lot_code: string; accepted: string; damaged: string; quarantined: string };
type ReceiptLineBody = {
  sku: string; lot_code: string; accepted_qty: number; damaged_qty: number; quarantined_qty: number;
};
type ReceiptBody = { receipt_key: string; lines: ReceiptLineBody[] };
export type StagedReceiptResult = {
  staged_receipt_id: string; status: string; replayed: boolean; inventory_posted: boolean;
};

const EMPTY_LINE: ReceiptLineDraft = { lot_code: '', accepted: '0', damaged: '0', quarantined: '0' };

function remainingCases(line: PurchaseLine): number {
  return Number(line.qty_ordered) - Number(line.qty_received);
}

/** Submit what physically arrived for review (POST /orders/{po}/receipts). Stock is not changed. */
export function RecordReceiptForm({ poNumber, lines, onRecorded }: {
  poNumber: string;
  lines: PurchaseLine[];
  onRecorded: (result: StagedReceiptResult) => void;
}) {
  const [receiptKey, setReceiptKey] = useState('');
  const [drafts, setDrafts] = useState<Record<string, ReceiptLineDraft>>({});
  const [problem, setProblem] = useState('');
  const [pending, setPending] = useState<ReceiptBody | null>(null);
  const write = useConfirmedWrite<StagedReceiptResult>();
  const keyId = useId();

  function draftFor(sku: string): ReceiptLineDraft {
    return drafts[sku] ?? EMPTY_LINE;
  }
  function update(sku: string, change: Partial<ReceiptLineDraft>) {
    setDrafts((previous) => ({ ...previous, [sku]: { ...(previous[sku] ?? EMPTY_LINE), ...change } }));
  }

  function review() {
    const key = receiptKey.trim();
    setPending(null);
    if (!key || key.length > 150) {
      setProblem('Enter a receipt reference such as the packing slip or delivery number.');
      return;
    }
    const body: ReceiptLineBody[] = [];
    for (const line of lines) {
      const draft = draftFor(line.sku);
      const accepted = parseCases(draft.accepted);
      const damaged = parseCases(draft.damaged);
      const quarantined = parseCases(draft.quarantined);
      if (accepted === null || damaged === null || quarantined === null) {
        setProblem(`${line.sku}: enter whole numbers of cases (0 or more).`);
        return;
      }
      const total = accepted + damaged + quarantined;
      if (total === 0) continue;
      if (total > remainingCases(line)) {
        setProblem(`${line.sku}: ${total} cases is more than the ${remainingCases(line)} still expected on this order.`);
        return;
      }
      if (draft.lot_code.trim().length > 120) {
        setProblem(`${line.sku}: the lot code is too long.`);
        return;
      }
      body.push({
        sku: line.sku, lot_code: draft.lot_code.trim(),
        accepted_qty: accepted, damaged_qty: damaged, quarantined_qty: quarantined,
      });
    }
    if (body.length === 0) {
      setProblem('Enter the cases that arrived for at least one product.');
      return;
    }
    setProblem('');
    setPending({ receipt_key: key, lines: body });
    write.open();
  }

  async function confirm() {
    if (!pending) return;
    const result = await write.send(() => api.post<StagedReceiptResult>(
      `/api/logistics/orders/${encodeURIComponent(poNumber)}/receipts`, pending));
    if (result) onRecorded(result);
  }

  const sum = (key: 'accepted_qty' | 'damaged_qty' | 'quarantined_qty') =>
    pending?.lines.reduce((total, line) => total + line[key], 0) ?? 0;

  return <form className={CARD} aria-label="Record a delivery"
    onSubmit={(event) => { event.preventDefault(); review(); }}>
    <h4 className="font-semibold text-slate-900">Record a delivery</h4>
    <p className="text-sm text-slate-600">
      Enter what physically arrived. This is sent for approval; stock only changes after a different owner or manager approves it.
    </p>
    <Field label="Receipt reference" htmlFor={keyId} help="For example the packing slip or delivery note number.">
      <input id={keyId} className={INPUT} value={receiptKey} maxLength={150}
        onChange={(event) => setReceiptKey(event.target.value)} />
    </Field>
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left">
        <thead><tr className="text-slate-600">
          <th className="p-2">SKU</th><th className="p-2">Still expected</th><th className="p-2">Lot code</th>
          <th className="p-2">Good cases</th><th className="p-2">Damaged</th><th className="p-2">Hold for inspection</th>
        </tr></thead>
        <tbody>{lines.map((line) => {
          const draft = draftFor(line.sku);
          return <tr key={line.sku} className="border-t border-slate-200">
            <td className="p-2 font-medium">{line.sku}</td>
            <td className="p-2">{remainingCases(line)}</td>
            <td className="p-2"><input aria-label={`${line.sku} lot code`} className={INPUT} value={draft.lot_code}
              onChange={(event) => update(line.sku, { lot_code: event.target.value })} /></td>
            <td className="p-2"><input aria-label={`${line.sku} good cases`} className={INPUT} inputMode="numeric"
              value={draft.accepted} onChange={(event) => update(line.sku, { accepted: event.target.value })} /></td>
            <td className="p-2"><input aria-label={`${line.sku} damaged cases`} className={INPUT} inputMode="numeric"
              value={draft.damaged} onChange={(event) => update(line.sku, { damaged: event.target.value })} /></td>
            <td className="p-2"><input aria-label={`${line.sku} cases held for inspection`} className={INPUT} inputMode="numeric"
              value={draft.quarantined} onChange={(event) => update(line.sku, { quarantined: event.target.value })} /></td>
          </tr>;
        })}</tbody>
      </table>
    </div>
    {problem && <p role="alert" className="text-sm text-red-700">{problem}</p>}
    {!write.confirming && <button type="submit" className={BUTTON_PRIMARY}>Review delivery</button>}
    {write.confirming && pending && <ConfirmPanel title="Submit this delivery for approval?"
      confirmLabel="Submit for approval" busy={write.busy} onConfirm={confirm} onCancel={write.cancel}>
      <p>
        Receipt <strong>{pending.receipt_key}</strong> for purchase order <strong>{poNumber}</strong>:
        {' '}{sum('accepted_qty')} good, {sum('damaged_qty')} damaged and {sum('quarantined_qty')} held for inspection
        across {pending.lines.length} product{pending.lines.length === 1 ? '' : 's'}.
      </p>
      <p>
        This saves the delivery for review only. Your stock does not change yet: a different owner or manager must
        approve it first. Nothing is sent to the supplier or QuickBooks. The receipt reference cannot be reused for different quantities.
      </p>
    </ConfirmPanel>}
    {Boolean(write.error) && <ErrorNote error={write.error} fallback="The delivery was not recorded." />}
  </form>;
}
