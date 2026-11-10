import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ActionPreview, Receipt } from '../contract';
import type { LolaTransport } from '../transport';
import { RECEIPT_POLL_DELAYS_MS, setTransport, useLolaStore } from '../store';

const action: ActionPreview = {
  id: 'act-1', kind: 'email', title: 'Send reminder', summary: 'x', risk: 'low',
  can_approve: true, status: 'proposed',
} as unknown as ActionPreview;

function seed() {
  useLolaStore.setState({
    messages: [{
      id: 'l-1', role: 'lola', text: 'Draft ready', language: 'en', channel: 'text', status: 'complete',
      cards: [], actions: [{ ...action }], receipts: [], notices: [], suggestions: [],
    } as never],
  });
}
const msg = () => useLolaStore.getState().messages[0] as unknown as {
  actions: ActionPreview[]; receipts: Receipt[]; notices: { message: string }[];
};

function transport(over: Partial<LolaTransport>): LolaTransport {
  return {
    turn: async function* () { /* unused */ },
    approve: async () => ({ action_id: 'act-1', status: 'submitted', verified: false, message: 'Queued' }),
    reject: async () => {},
    briefing: async () => { throw new Error('unused'); },
    ...over,
  } as LolaTransport;
}

describe('submitted receipts are followed until the ledger answers', () => {
  beforeEach(() => { vi.useFakeTimers(); seed(); });
  afterEach(() => { useLolaStore.getState().newConversation(); vi.useRealTimers(); });

  it('turns into executed only when the server verifies it', async () => {
    const answers: Receipt[] = [
      { action_id: 'act-1', status: 'submitted', verified: false, message: 'Still queued' },
      { action_id: 'act-1', status: 'executed', verified: true, message: 'Sent', reference: 'email:abc' },
    ];
    const getReceipt = vi.fn(async () => answers.shift() ?? null);
    setTransport(transport({ getReceipt }));
    await useLolaStore.getState().approve('l-1', 'act-1');
    expect(msg().actions[0].status).toBe('submitted');
    await vi.advanceTimersByTimeAsync(RECEIPT_POLL_DELAYS_MS[0]);
    expect(msg().actions[0].status).toBe('submitted');
    await vi.advanceTimersByTimeAsync(RECEIPT_POLL_DELAYS_MS[1]);
    expect(msg().actions[0].status).toBe('executed');
    expect(msg().receipts[0].reference).toBe('email:abc');
    await vi.advanceTimersByTimeAsync(600_000);
    expect(getReceipt).toHaveBeenCalledTimes(2);                 // stops once final
  });

  it('an executed-but-unverified answer is never shown as done', async () => {
    setTransport(transport({ getReceipt: async () => ({ action_id: 'act-1', status: 'executed', verified: false, message: '?' }) }));
    await useLolaStore.getState().approve('l-1', 'act-1');
    await vi.advanceTimersByTimeAsync(RECEIPT_POLL_DELAYS_MS[0]);
    expect(msg().actions[0].status).toBe('submitted');
  });

  it('shows a failure reported later by the ledger', async () => {
    setTransport(transport({ getReceipt: async () => ({ action_id: 'act-1', status: 'failed', verified: true, message: 'Provider rejected it' }) }));
    await useLolaStore.getState().approve('l-1', 'act-1');
    await vi.advanceTimersByTimeAsync(RECEIPT_POLL_DELAYS_MS[0]);
    expect(msg().actions[0].status).toBe('failed');
    expect(msg().receipts[0].message).toMatch(/rejected/);
  });

  it('keeps trying through errors and gives up after the schedule', async () => {
    const getReceipt = vi.fn(async () => { throw new Error('offline'); });
    setTransport(transport({ getReceipt }));
    await useLolaStore.getState().approve('l-1', 'act-1');
    await vi.advanceTimersByTimeAsync(RECEIPT_POLL_DELAYS_MS.reduce((a, b) => a + b, 0) + 60_000);
    expect(getReceipt).toHaveBeenCalledTimes(RECEIPT_POLL_DELAYS_MS.length);
    expect(msg().actions[0].status).toBe('submitted');
  });
});

describe('dismissing an action', () => {
  beforeEach(seed);

  it('is marked rejected only after the server confirms', async () => {
    let done!: () => void;
    setTransport(transport({ reject: () => new Promise<void>((r) => { done = r; }) }));
    const p = useLolaStore.getState().reject('l-1', 'act-1');
    expect(msg().actions[0].status).toBe('proposed');
    done();
    await p;
    expect(msg().actions[0].status).toBe('rejected');
  });

  it('stays actionable and says so when the server refuses or is unreachable', async () => {
    setTransport(transport({ reject: async () => { throw new Error('Network error'); } }));
    await useLolaStore.getState().reject('l-1', 'act-1');
    expect(msg().actions[0].status).toBe('proposed');
    expect(msg().notices[msg().notices.length - 1]?.message).toMatch(/still waiting for approval/);
  });
});
